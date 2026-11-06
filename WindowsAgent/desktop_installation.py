"""Explicit first-use installation and Windows Apps ownership records."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid

from app_paths import FROZEN, EXECUTABLE, child_environment
from app_version import APP_VERSION

APP_ID = 'lumiao.chatgpt.remote'
MARKER = '.chatgptremote-install.json'
REDIRECT = '.chatgptremote-location'
APP_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\ChatGPTRemote'


def plain_path(value):
    path = Path(value).expanduser().absolute()
    if path.parent == path or str(path).startswith('\\\\'):
        raise ValueError('请选择本机的程序文件夹。')
    for current in (path, *path.parents):
        try:
            info = current.lstat()
            attributes = getattr(info, 'st_file_attributes', 0)
        except FileNotFoundError: continue
        if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('程序文件夹不能使用链接或重定向目录。')
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise ValueError('程序文件不能使用硬链接。')
    return path.resolve()


def atomic_json(path, value):
    path = plain_path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def installation_record(root):
    root = plain_path(root)
    try: value = json.loads(plain_path(root / MARKER).read_text(encoding='utf-8'))
    except (OSError,ValueError): return None
    if not isinstance(value,dict) or value.get('app') != APP_ID or value.get('schema') != 1:
        return None
    import re
    if value.get('root') != str(root) or not isinstance(value.get('id'),str) or not re.fullmatch('[0-9a-f]{32}',value['id']):
        return None
    return value


def register_application(executable):
    import winreg
    executable = Path(executable).resolve()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,APP_KEY) as key:
            old = winreg.QueryValueEx(key,'InstallLocation')[0]
            if Path(old).resolve() != executable.parent:
                raise ValueError('已在其他位置安装 ChatGPT Remote，请从原程序更新或先卸载。')
    except FileNotFoundError: pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,APP_KEY) as key:
        for name,value in {'DisplayName':'ChatGPT Remote','DisplayVersion':APP_VERSION,'Publisher':'LUMIAO9527',
                           'InstallLocation':str(executable.parent),'DisplayIcon':str(executable),
                           'UninstallString':'"'+str(executable)+'" --uninstall',
                           'URLInfoAbout':'https://github.com/LUMIAO9527/ChatGPT-Remote'}.items():
            winreg.SetValueEx(key,name,0,winreg.REG_SZ,value)
        for name in ('NoModify','NoRepair'): winreg.SetValueEx(key,name,0,winreg.REG_DWORD,1)


def refresh_application(executable, *, log=None):
    """Refresh changed metadata without making Windows registration a startup gate."""
    import winreg
    executable = Path(executable).resolve()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_KEY) as key:
            location = winreg.QueryValueEx(key, 'InstallLocation')[0]
            if Path(location).resolve() != executable.parent:
                raise ValueError('已在其他位置安装 ChatGPT Remote。')
            version = winreg.QueryValueEx(key, 'DisplayVersion')[0]
            if version == APP_VERSION:
                return True
    except FileNotFoundError:
        pass
    except PermissionError as error:
        if log: log.warning('registration_read_deferred code=%s', error.winerror)
        return False
    try:
        register_application(executable)
        return True
    except OSError as error:
        if log: log.warning('registration_write_deferred code=%s', getattr(error, 'winerror', None))
        return False


class Installation:
    def __init__(self, executable=EXECUTABLE, *, frozen=FROZEN, register=register_application):
        self.executable = Path(executable).resolve()
        self.root = self.executable.parent
        self.frozen, self.register = frozen, register

    def status(self):
        record = installation_record(self.root) if self.frozen else None
        suggested = Path('D:/ChatGPTRemote') if Path('D:/').is_dir() else Path.home()/'ChatGPTRemote'
        return {'firstRun':self.frozen and record is None,'sourceMode':not self.frozen,
                'suggested':str(suggested),'current':str(self.root),'installed':bool(record)}

    def install(self, folder):
        if not self.frozen: raise ValueError('开发源码不需要安装，请使用发布的独立程序。')
        if not isinstance(folder,str) or not folder.strip(): raise ValueError('请选择程序文件夹。')
        root = plain_path(folder.strip())
        executable = root / 'ChatGPTRemote.exe'
        existing = installation_record(root)
        retained = bool(existing and not executable.exists())
        if existing and not retained:
            raise ValueError('该文件夹已有安装，请打开原程序使用版本更新。')
        if executable.exists() and executable != self.executable:
            raise ValueError('该文件夹已有同名文件，请更换文件夹。')
        if (root / MARKER).exists() and not retained: raise ValueError('该文件夹的安装记录不完整，请更换文件夹。')
        if not retained and ((root / '_data').exists() or (root / '_wip/desktop-runtime').exists()):
            raise ValueError('该文件夹已有运行数据，请选择新的程序文件夹。')
        root.mkdir(parents=True,exist_ok=True)
        copied = executable != self.executable
        temporary = root / ('.rm-install-'+uuid.uuid4().hex+'.exe')
        record = {**(existing or {}),'schema':1,'app':APP_ID,'id':existing['id'] if retained else uuid.uuid4().hex,'root':str(root),'version':APP_VERSION}
        committed = False
        try:
            if copied:
                with self.executable.open('rb') as source, temporary.open('xb') as target:
                    for chunk in iter(lambda:source.read(256*1024),b''): target.write(chunk)
                if hashlib.sha256(temporary.read_bytes()).digest()!=hashlib.sha256(self.executable.read_bytes()).digest():
                    raise ValueError('程序复制校验未通过，请重试。')
                os.replace(temporary,executable)
            atomic_json(root/MARKER,record)
            self.register(executable)
            committed = True
            if copied:
                try: atomic_json(self.root/REDIRECT,{'schema':1,'app':APP_ID,'root':str(root),'id':record['id']})
                except OSError: pass
            return {'root':str(root),'executable':str(executable),'version':APP_VERSION}
        finally:
            temporary.unlink(missing_ok=True)
            if not committed:
                if (root/MARKER).is_file() and installation_record(root)==record:
                    if existing: atomic_json(root/MARKER,existing)
                    else: (root/MARKER).unlink()
                if copied and executable.is_file() and hashlib.sha256(executable.read_bytes()).digest()==hashlib.sha256(self.executable.read_bytes()).digest():
                    executable.unlink()

    def redirected_executable(self):
        if not self.frozen or (self.root/MARKER).exists(): return None
        try:
            redirect=json.loads(plain_path(self.root/REDIRECT).read_text(encoding='utf-8'))
            if redirect.get('app')!=APP_ID or redirect.get('schema')!=1:return None
            root=plain_path(redirect['root']);record=installation_record(root)
            target=root/'ChatGPTRemote.exe'
            if root!=self.root and record and record['id']==redirect.get('id') and target.is_file(): return target
        except (OSError,ValueError,KeyError,TypeError): pass
        return None

    @staticmethod
    def open_installed(executable, arguments=None):
        environment=child_environment()
        temporary=Path(executable).parent/'_wip/desktop-runtime/temp'
        temporary.mkdir(parents=True,exist_ok=True)
        environment['TEMP']=environment['TMP']=str(temporary)
        subprocess.Popen([str(executable),*(arguments or ['--show'])],cwd=str(Path(executable).parent),env=environment,
                         stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
