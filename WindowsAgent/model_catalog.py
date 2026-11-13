"""Read account-scoped model capabilities and effort visibility without UI input.

Credentials stay in memory. Only two fixed, read-only OpenAI endpoints are used;
no authentication refresh, preference update, or model inference is performed.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.request


LABELS = {
    'zh-CN': {'none': '无', 'minimal': '极低', 'low': '轻度', 'medium': '中', 'high': '高', 'xhigh': '极高',
              'max': 'Max', 'ultra': 'Ultra'},
    'en': {'none': 'None', 'minimal': 'Minimal', 'low': 'Light', 'medium': 'Medium', 'high': 'High', 'xhigh': 'Extra High',
           'max': 'Max', 'ultra': 'Ultra'},
}
DEFAULT_EFFORTS = ('low', 'medium', 'high', 'xhigh')


class CatalogUnavailable(Exception):
    pass


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def read_json(path: Path, limit: int) -> dict:
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise CatalogUnavailable('账号配置暂时不可用')
    result = json.loads(data)
    if not isinstance(result, dict):
        raise CatalogUnavailable('账号配置暂时不可用')
    return result


@dataclass(frozen=True)
class Account:
    signature: str
    owner: str
    mode: str
    titles: dict[str, str]
    version: str
    account_id: str = field(repr=False)
    token: str = field(repr=False)
    user_id: str = field(default='', repr=False)
    local_models: dict | None = field(default=None, repr=False)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class ModelCatalog:
    def __init__(self, home: Path | None = None, fetch=None, clock=time.monotonic):
        self.home = home or Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        self.fetch = fetch or self._fetch
        self.clock = clock
        self.lock = threading.RLock()
        self.cached = None

    def account(self) -> Account:
        try:
            state = read_json(self.home / '.codex-global-state.json', 8_000_000)
            auth = read_json(self.home / 'auth.json', 64_000)
            tokens = auth.get('tokens', {})
            if auth.get('auth_mode') != 'chatgpt':
                raise ValueError()
            token = tokens['access_token']
            encoded = token.split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
            principal = claims['https://api.openai.com/auth']
            user, account_id = principal['chatgpt_user_id'], principal['chatgpt_account_id']
            if (not isinstance(user, str) or not isinstance(account_id, str) or
                    tokens.get('account_id') != account_id or claims.get('exp', 0) <= time.time()):
                raise ValueError()
            owner = digest([user, account_id])
            local_models = None
            if 'electron-renderer-content-owner' in state:
                if state['electron-renderer-content-owner'] != owner:
                    raise ValueError()
                atoms = state['electron-owner-atoms:' + owner]
            else:
                if state.get('electron-account-renderer-state-v1', {}).get('owner') != owner:
                    raise ValueError()
                atoms = state['electron-persisted-atom-state']
            response = atoms['chatgpt-work-models-v1/' + user + '/' + account_id]['response']
            eligible = {slug.removesuffix('-wm') for slug in response['eligible_codex_model_slugs'] if isinstance(slug, str)}
            titles = {m['slug'].removesuffix('-wm'): m['title'] for m in response['models']
                      if isinstance(m.get('slug'), str) and isinstance(m.get('title'), str)
                      and m['slug'].removesuffix('-wm') in eligible}
            if not titles or len(titles) > 200:
                raise ValueError()
            if 'electron-renderer-content-owner' in state:
                aliases = {'min': 'low', 'standard': 'medium', 'extended': 'high'}
                local_models = {}
                for model in response['models']:
                    slug = str(model.get('slug', '')).removesuffix('-wm')
                    if slug not in titles:
                        continue
                    levels = [entry.get('thinking_effort') for entry in model.get('thinking_efforts', [])
                              if isinstance(entry, dict) and isinstance(entry.get('thinking_effort'), str)]
                    mapped = [aliases.get(level, level) for level in levels]
                    if mapped and len(set(mapped)) == len(mapped) and all(level in LABELS['en'] for level in mapped):
                        local_models[slug] = mapped
            mode = atoms.get('composer-model-picker-selection-mode-v1', '')
            # Read only the producer version. Capabilities come from an
            # authenticated account-scoped response, never a shared cache.
            cache = read_json(self.home / 'models_cache.codex_work_desktop.json', 4_000_000)
            version = cache['client_version']
            if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:[-.][a-zA-Z0-9.-]+)?', version):
                raise ValueError()
            signature = digest([owner, mode, titles, local_models, version, hashlib.sha256(token.encode()).hexdigest()])
            return Account(signature, owner, mode, titles, version, account_id, token, user, local_models)
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise CatalogUnavailable('账号正在切换或配置未同步，请重新读取') from None

    @staticmethod
    def _fetch(account: Account, kind: str) -> dict:
        paths = {'settings': '/settings/user',
                 'models': '/codex/models?client_version=' + account.version}
        request = urllib.request.Request('https://chatgpt.com/backend-api' + paths[kind], method='GET',
            headers={'Authorization': 'Bearer ' + account.token, 'ChatGPT-Account-ID': account.account_id,
                     'Accept': 'application/json', 'originator': 'codex_work_desktop',
                     'User-Agent': 'ChatGPTRemote/1.1'})
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=3) as response:
                body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ValueError()
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except Exception:
            # Never include URLs, response bodies, or request headers in errors.
            raise CatalogUnavailable('暂未读到账号档位，请重新读取') from None

    def read(self, force=False) -> dict:
        with self.lock:
            account = self.account()
            cached = self.cached
            now = self.clock()
            same = cached is not None and cached['account'] == account.signature
            if same and not force and now - cached['settingsAt'] < 20:
                return cached
            if account.local_models is not None:
                # Current Work clients persist their offered levels per model.
                # Enrich still compares that list to the active native slider.
                models = account.local_models
                enabled = list(dict.fromkeys(level for levels in models.values() for level in levels))
                self.cached = {'account': account.signature, 'owner': account.owner, 'mode': account.mode,
                               'titles': account.titles, 'enabled': enabled, 'models': models,
                               'revision': digest([account.signature, enabled, models])[:24],
                               'settingsAt': now, 'modelsAt': now}
                return self.cached
            reuse_models = same and now - cached['modelsAt'] < 120
            if reuse_models:
                settings = self.fetch(account, 'settings')
                models, models_at = cached['models'], cached['modelsAt']
            else:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    settings_job = pool.submit(self.fetch, account, 'settings')
                    models_job = pool.submit(self.fetch, account, 'models')
                    settings, model_response = settings_job.result(), models_job.result()
                models = {}
                for model in model_response.get('models', []):
                    slug = model.get('slug')
                    if slug not in account.titles or model.get('visibility') != 'list':
                        continue
                    efforts = [level.get('effort') for level in model.get('supported_reasoning_levels', [])
                               if level.get('effort') != 'persistent']
                    if efforts and len(set(efforts)) == len(efforts) and all(e in LABELS['en'] for e in efforts):
                        models[slug] = efforts
                models_at = now
            preferences = settings.get('settings')
            if not isinstance(preferences, dict):
                raise CatalogUnavailable('账号档位配置暂未识别，请重新读取')
            visibility = preferences.get('model_picker_reasoning_effort_visibility')
            enabled = (list(DEFAULT_EFFORTS) if visibility is None else
                       visibility.get('enabled') if isinstance(visibility, dict) else None)
            if (not isinstance(enabled, list) or len(enabled) > 12 or
                    any(not isinstance(e, str) or (e not in LABELS['en'] and e != 'persistent') for e in enabled) or
                    len(set(enabled)) != len(enabled)):
                raise CatalogUnavailable('账号档位配置暂未识别，请重新读取')
            if self.account().signature != account.signature:
                self.cached = None
                raise CatalogUnavailable('账号已变化，请重新读取')
            revision = digest([account.signature, enabled, models])[:24]
            result = {'account': account.signature, 'owner': account.owner, 'mode': account.mode, 'titles': account.titles,
                      'enabled': enabled, 'models': models, 'revision': revision,
                      'settingsAt': now, 'modelsAt': models_at}
            self.cached = result
            return result

    @staticmethod
    def model_key(title: str, titles: dict, *, announcement=False) -> str | None:
        def normalize(name):
            return re.sub(r'[-\s]+', ' ', name.strip().casefold()).removeprefix('gpt ')

        name = normalize(title)
        matches = [(len(label), slug) for slug, title in titles.items()
                   if (label := normalize(title)) and (name == label or announcement and name.startswith(label + ' '))]
        matches.sort(reverse=True)
        if matches and (len(matches) == 1 or matches[0][0] > matches[1][0]):
            return matches[0][1]
        return None

    @staticmethod
    def profiles(data: dict, locale: str) -> dict:
        labels = LABELS.get(locale, {})
        return {slug: [{'key': effort, 'label': labels[effort]} for effort in levels
                       if effort in data['enabled'] and effort in labels]
                for slug, levels in data['models'].items()}

    def state_from_root(self, raw: dict, data: dict) -> dict | None:
        title = str(raw.get('rootModel') or '')
        slug = self.model_key(title, data['titles'], announcement=True)
        if not slug:
            return None
        localized = any(re.search(r'[\u4e00-\u9fff]', str(item.get('title', ''))) for item in raw.get('items', []))
        locale = 'zh-CN' if localized else 'en'
        choices = [locale] + [name for name in LABELS if name != locale]
        for name in choices:
            for effort, label in LABELS[name].items():
                if title.endswith(' ' + label):
                    return {'model': slug, 'effort': effort, 'locale': name, 'mode': data['mode'],
                            'label': data['titles'][slug], 'effortLabel': label, 'verified': False}
        return None

    def enrich(self, raw: dict) -> dict:
        if not raw.get('ok') or not raw.get('menu') or raw.get('prompt'):
            return raw
        powers = [i for i in raw.get('items', []) if i.get('kind') == 'power-step']
        if not powers and raw.get('category') != 'model':
            return raw
        result = {**raw, 'items': [dict(i) for i in raw.get('items', [])]}
        try:
            account = self.account()
        except CatalogUnavailable:
            account = None
        # Configuration actions are not models. Match the account's complete
        # model titles rather than trying to blacklist UI wording.
        for item in result['items']:
            if item.get('kind') != 'option':
                continue
            slug = self.model_key(item['title'], account.titles) if account else None
            if slug:
                item['modelKey'] = slug
                item['modelLabel'] = account.titles[slug]
            else:
                item['kind'] = 'configuration'
        if len(powers) != 1:
            if not powers and raw.get('rootModel'):
                try:
                    data = self.read()
                    current = self.state_from_root(raw, data)
                    if current:
                        result['modelState'] = current
                        result['modelOwner'] = data['owner']
                except CatalogUnavailable:
                    pass
            return result
        try:
            data = self.read()
            native = powers[0]
            labels = LABELS.get(native.get('locale'))
            slug = self.model_key(native.get('title', ''), data['titles'], announcement=True)
            if not labels or not slug:
                raise CatalogUnavailable('暂未识别电脑档位，请重新读取')
            effort_key = next((key for key, label in labels.items() if label == native.get('label')), None)
            result['modelState'] = {'model': slug, 'effort': effort_key, 'locale': native['locale'],
                                    'mode': data['mode'], 'label': data['titles'][slug],
                                    'effortLabel': native.get('label')}
            result['modelOwner'] = data['owner']
            if data['mode'] != 'model':
                raise CatalogUnavailable('选择具体模型后可调整思考强度')
            levels = [e for e in data['models'].get(slug, []) if e in data['enabled']]
            count, current = native.get('levelCount'), native.get('currentLevel')
            if (not levels or type(current) is not int or len(levels) != count or
                    not 1 <= current <= count or labels[levels[current - 1]] != native.get('label')):
                raise CatalogUnavailable('电脑档位与账号配置尚未一致，请重新读取')
            result['modelState']['verified'] = True
            replacement, actions = [], {}
            for index, effort in enumerate(levels, 1):
                item = {**native, 'id': native['id'] + '|target:' + effort,
                        'title': data['titles'][slug] + ' ' + labels[effort], 'label': labels[effort],
                        'effortKey': effort, 'level': index, 'enabled': bool(native.get('adjustable', True)), 'selected': index == current}
                replacement.append(item)
                actions[item['id']] = {'revision': data['revision'], 'native': native,
                    'targetLevel': index, 'targetEffort': effort, 'targetLabel': labels[effort],
                    'modelTitle': native.get('modelTitle') or data['titles'][slug],
                    'labels': [labels[e] for e in levels]}
            result['items'] = [i for i in result['items'] if i.get('kind') != 'power-step'] + replacement
            result['powerActions'] = actions
        except CatalogUnavailable as error:
            result['powerNotice'] = str(error)
        return result

    def validate(self, action: dict, *, prechecked=False) -> None:
        data = self.read(force=not prechecked)
        if data['mode'] != 'model' or data['revision'] != action['revision']:
            raise CatalogUnavailable('账号或档位配置已变化，请重新读取后再选择')
