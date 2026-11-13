"""Account isolation and explicit-action contracts; no real account/UI access."""
import base64
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_catalog import ModelCatalog, CatalogUnavailable, LABELS, digest
from conversation import ConversationReader
from windows_control import TargetError

EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        claims = {'exp': 9_999_999_999, 'https://api.openai.com/auth':
                  {'chatgpt_user_id': 'fixture-user', 'chatgpt_account_id': 'fixture-account'}}
        self.token = 'fixture.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.signature'
        self.files = {
            'auth.json': {'auth_mode': 'chatgpt', 'tokens':
                          {'account_id': 'fixture-account', 'access_token': self.token}},
            '.codex-global-state.json': {
                'electron-account-renderer-state-v1': {'owner': digest(['fixture-user', 'fixture-account'])},
                'electron-persisted-atom-state': {
                    'composer-model-picker-selection-mode-v1': 'model',
                    'chatgpt-work-models-v1/fixture-user/fixture-account': {'response': {
                        'eligible_codex_model_slugs': ['fixture-sol', 'fixture-luna'],
                        'models': [{'slug': 'fixture-sol-wm', 'title': 'GPT Fixture Sol'},
                                   {'slug': 'fixture-luna-wm', 'title': 'GPT Fixture Luna'}]}}}},
            'models_cache.codex_work_desktop.json': {'client_version': '0.167.0'}}
        self.preferences = {'settings': {'model_picker_reasoning_effort_visibility': {'enabled': EFFORTS.copy()}}}
        self.models = {'models': [
            {'slug': 'fixture-sol', 'visibility': 'list', 'supported_reasoning_levels': [{'effort': e} for e in EFFORTS]},
            {'slug': 'fixture-luna', 'visibility': 'list', 'supported_reasoning_levels': [{'effort': e} for e in EFFORTS[:-1]]},
            {'slug': 'hidden', 'visibility': 'hide', 'supported_reasoning_levels': [{'effort': 'max'}]}]}
        self.fetch = Mock(side_effect=lambda account, kind: copy.deepcopy(self.preferences if kind == 'settings' else self.models))
        self.read_patch = patch('model_catalog.read_json', side_effect=lambda path, limit: copy.deepcopy(self.files[path.name]))
        self.read_patch.start(); self.addCleanup(self.read_patch.stop)
        self.catalog = ModelCatalog(Path('fixture-home'), fetch=self.fetch, clock=lambda: 100)

    def raw(self, count=6, current=5, effort='max', model='GPT Fixture Sol', locale='zh-CN'):
        label = LABELS[locale][effort]
        return {'ok': True, 'identity': 'fixture-window|fixture-chat', 'context': 'menu', 'category': 'model', 'menu': True,
                'items': [{'id': 'native|effort:' + str(current), 'title': model + ' ' + label, 'kind': 'power-step',
                           'label': label, 'locale': locale, 'enabled': False, 'selected': True,
                           'level': current, 'currentLevel': current, 'levelCount': count}]}

    def test_read_generates_six_true_names_without_any_ui_operation(self):
        result = self.catalog.enrich(self.raw())
        self.assertEqual([i['label'] for i in result['items']], ['轻度', '中', '高', '极高', 'Max', 'Ultra'])
        self.assertEqual([i['level'] for i in result['items']], list(range(1, 7)))
        self.assertTrue(all(i['enabled'] for i in result['items']))
        self.assertEqual([i['effortKey'] for i in result['items'] if i['selected']], ['max'])
        self.assertEqual({c.args[1] for c in self.fetch.call_args_list}, {'settings', 'models'})

    def test_hidden_max_is_omitted_even_when_ultra_is_enabled(self):
        self.preferences['settings']['model_picker_reasoning_effort_visibility']['enabled'].remove('max')
        result = self.catalog.enrich(self.raw(count=5, current=5, effort='ultra'))
        self.assertEqual([i['effortKey'] for i in result['items']], ['low', 'medium', 'high', 'xhigh', 'ultra'])

    def test_arbitrary_hidden_middle_tiers_keep_the_exact_order(self):
        self.preferences['settings']['model_picker_reasoning_effort_visibility']['enabled'] = ['ultra', 'low', 'high']
        result = self.catalog.enrich(self.raw(count=3, current=2, effort='high'))
        self.assertEqual([i['label'] for i in result['items']], ['轻度', '高', 'Ultra'])

    def test_model_capabilities_remove_unsupported_ultra(self):
        result = self.catalog.enrich(self.raw(count=5, model='GPT Fixture Luna'))
        self.assertEqual([i['effortKey'] for i in result['items']], EFFORTS[:-1])

    def test_missing_visibility_uses_the_desktop_default_four(self):
        self.preferences = {'settings': {}}
        result = self.catalog.enrich(self.raw(count=4, current=2, effort='medium', locale='en'))
        self.assertEqual([i['label'] for i in result['items']], ['Light', 'Medium', 'High', 'Extra High'])

    def test_native_count_or_caption_mismatch_never_enables_a_guess(self):
        for raw in [self.raw(count=5), self.raw(current=4), self.raw(model='Unrecognized model')]:
            with self.subTest(raw=raw):
                result = self.catalog.enrich(raw)
                self.assertFalse(result['items'][0]['enabled'])
                self.assertNotIn('powerActions', result)
                self.assertIn('powerNotice', result)

    def test_combined_default_slider_does_not_become_an_effort_only_control(self):
        self.files['.codex-global-state.json']['electron-persisted-atom-state']['composer-model-picker-selection-mode-v1'] = 'default'
        result = self.catalog.enrich(self.raw())
        self.assertFalse(result['items'][0]['enabled']); self.assertNotIn('powerActions', result)

    def test_configuration_text_is_filtered_by_real_model_titles(self):
        raw = self.raw()
        raw['items'] += [{'id': t, 'title': t, 'kind': 'option', 'enabled': True} for t in
                         ['默认 推荐模型集', '默认', '推荐', 'GPT Fixture Sol', 'not-a-model']]
        result = self.catalog.enrich(raw)
        self.assertEqual([i['title'] for i in result['items'] if i['kind'] == 'option'], ['GPT Fixture Sol'])

    def test_native_titles_match_with_or_without_the_visual_gpt_prefix(self):
        raw = self.raw()
        raw['items'] = [{'id': 'sol', 'title': 'Fixture Sol', 'kind': 'option', 'enabled': True}]
        result = self.catalog.enrich(raw)
        self.assertEqual(result['items'][0]['modelKey'], 'fixture-sol')
        self.assertEqual(result['items'][0]['modelLabel'], 'GPT Fixture Sol')

    def test_open_model_list_reads_current_state_from_the_native_composer(self):
        raw = self.raw()
        raw['rootModel'] = 'GPT Fixture Sol Max'
        raw['items'] = [{'id': 'config', 'title': '默认 推荐模型集', 'kind': 'option', 'enabled': True}]
        result = self.catalog.enrich(raw)
        self.assertEqual(result['modelState']['model'], 'fixture-sol')
        self.assertEqual(result['modelState']['effort'], 'max')
        self.assertFalse(result['modelState']['verified'])
        self.assertNotIn('powerActions', result)

    def test_minimal_levels_and_persistent_metadata_keep_valid_model_tiers(self):
        self.preferences['settings']['model_picker_reasoning_effort_visibility']['enabled'] += ['none', 'minimal', 'persistent']
        self.models['models'][0]['supported_reasoning_levels'] = [{'effort': e} for e in ['none', 'minimal', 'low', 'persistent']]
        result = self.catalog.enrich(self.raw(count=3, current=1, effort='none'))
        self.assertEqual([item['effortKey'] for item in result['items']], ['none', 'minimal', 'low'])

    def test_native_disabled_slider_is_not_enabled_by_metadata(self):
        raw = self.raw()
        raw['items'][0]['adjustable'] = False
        result = self.catalog.enrich(raw)
        self.assertTrue(all(not item['enabled'] for item in result['items']))

    def test_scope_mismatch_never_uses_an_inactive_account_or_network(self):
        self.files['.codex-global-state.json']['electron-account-renderer-state-v1']['owner'] = 'other-account'
        with self.assertRaises(CatalogUnavailable): self.catalog.read()
        self.fetch.assert_not_called()

    def test_current_renderer_uses_only_its_account_atoms(self):
        state = self.files['.codex-global-state.json']
        owner = digest(['fixture-user', 'fixture-account'])
        state['electron-renderer-content-owner'] = owner
        state['electron-owner-atoms:' + owner] = state.pop('electron-persisted-atom-state')
        response = state['electron-owner-atoms:' + owner]['chatgpt-work-models-v1/fixture-user/fixture-account']['response']
        for model in response['models']:
            model['thinking_efforts'] = [{'thinking_effort': name} for name in ('min','standard','extended','xhigh','max','ultra')]
        state['electron-owner-atoms:other'] = {'composer-model-picker-selection-mode-v1': 'auto'}
        self.assertEqual(self.catalog.account().mode, 'model')
        self.assertEqual(set(self.catalog.read()['models']), {'fixture-sol', 'fixture-luna'})
        self.assertEqual(self.catalog.read()['models']['fixture-sol'], EFFORTS)
        self.fetch.assert_not_called()
        state['electron-renderer-content-owner'] = 'other'
        with self.assertRaises(CatalogUnavailable):
            self.catalog.account()

    def test_token_account_mismatch_is_rejected_before_network(self):
        self.files['auth.json']['tokens']['account_id'] = 'other-account'
        with self.assertRaises(CatalogUnavailable): self.catalog.read()
        self.fetch.assert_not_called()

    def test_account_change_while_reading_discards_the_response(self):
        original = self.catalog.account
        with patch.object(self.catalog, 'account', side_effect=[original(), CatalogUnavailable('账号已变化')]):
            with self.assertRaises(CatalogUnavailable): self.catalog.read()
        self.assertIsNone(self.catalog.cached)

    def test_cache_reduces_reads_but_apply_always_rechecks_preferences(self):
        action = next(iter(self.catalog.enrich(self.raw())['powerActions'].values()))
        self.assertEqual(self.fetch.call_count, 2)
        self.catalog.enrich(self.raw()); self.assertEqual(self.fetch.call_count, 2)
        self.catalog.validate(action)
        self.assertEqual([c.args[1] for c in self.fetch.call_args_list].count('settings'), 2)
        self.assertEqual([c.args[1] for c in self.fetch.call_args_list].count('models'), 1)

    def test_changed_visibility_blocks_stale_apply(self):
        action = next(iter(self.catalog.enrich(self.raw())['powerActions'].values()))
        self.preferences['settings']['model_picker_reasoning_effort_visibility']['enabled'].remove('max')
        with self.assertRaises(CatalogUnavailable): self.catalog.validate(action)

    def test_malformed_visibility_is_read_only(self):
        for value in [[], {'enabled': ['unknown']}, {'enabled': ['max', 'max']}, {'enabled': None}]:
            with self.subTest(value=value):
                self.catalog.cached = None
                self.preferences['settings']['model_picker_reasoning_effort_visibility'] = value
                result = self.catalog.enrich(self.raw())
                self.assertFalse(result['items'][0]['enabled']); self.assertNotIn('powerActions', result)

    def test_metadata_failure_preserves_current_native_value(self):
        self.fetch.side_effect = CatalogUnavailable('暂未读到账号档位')
        result = self.catalog.enrich(self.raw())
        self.assertEqual(result['items'][0]['label'], 'Max')
        self.assertFalse(result['items'][0]['enabled'])

    def test_public_options_do_not_include_credentials_or_private_action_plan(self):
        reader = ConversationReader(); reader.model_catalog = self.catalog
        result = reader._remember_options(42, self.raw())
        encoded = json.dumps(result)
        for private in [self.token, 'fixture-user', 'fixture-account', 'powerActions', 'targetLevel']:
            self.assertNotIn(private, encoded)

    def test_only_explicit_action_sends_the_exact_verified_native_plan(self):
        reader = ConversationReader(); reader.model_catalog = self.catalog
        reader._call = Mock(return_value={'ok': True})
        options = reader._remember_options(42, self.raw())
        reader._call.assert_not_called()
        light = next(i for i in options['items'] if i['effortKey'] == 'low')
        reader.composer_action(42, options['key'], light['id'])
        request = reader._call.call_args.kwargs
        self.assertEqual(request['operation'], 'composer_invoke')
        self.assertEqual(request['power']['targetLevel'], 1)
        self.assertEqual(request['power']['targetLabel'], '轻度')
        self.assertEqual(request['item']['id'], 'native|effort:5')
        with self.assertRaises(TargetError): reader.composer_action(42, options['key'], light['id'])
        self.assertEqual(reader._call.call_count, 1)

    def test_preference_change_blocks_native_input(self):
        reader = ConversationReader(); reader.model_catalog = self.catalog; reader._call = Mock()
        options = reader._remember_options(42, self.raw())
        self.preferences['settings']['model_picker_reasoning_effort_visibility']['enabled'].remove('max')
        with self.assertRaises(TargetError): reader.composer_action(42, options['key'], options['items'][0]['id'])
        reader._call.assert_not_called()

    def test_uncertain_native_input_is_not_repeated(self):
        reader = ConversationReader(); reader.model_catalog = self.catalog
        reader._call = Mock(side_effect=TargetError('timeout'))
        options = reader._remember_options(42, self.raw()); target = options['items'][0]['id']
        for _ in range(2):
            with self.assertRaises(TargetError): reader.composer_action(42, options['key'], target)
        self.assertEqual(reader._call.call_count, 1)

    def test_http_transport_is_get_only_fixed_origin_and_does_not_follow_redirects(self):
        account = self.catalog.account()
        response = Mock(); response.__enter__ = Mock(return_value=response); response.__exit__ = Mock()
        response.read.return_value = b'{"settings":{}}'
        opener = Mock(); opener.open.return_value = response
        with patch('model_catalog.urllib.request.build_opener', return_value=opener):
            ModelCatalog._fetch(account, 'settings')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.full_url, 'https://chatgpt.com/backend-api/settings/user')
        self.assertIsNone(request.data)
        self.assertNotIn(self.token, repr(account))


if __name__ == '__main__': unittest.main()
