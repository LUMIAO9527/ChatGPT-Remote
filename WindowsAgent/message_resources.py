"""Opaque, conversation-scoped references to files already present in messages."""
from __future__ import annotations
from collections import OrderedDict
import base64
import hashlib
import io
import mimetypes
import ntpath
from pathlib import Path
import secrets
import threading


class MessageResources:
    def __init__(self):
        self.key=secrets.token_bytes(32)
        self.items=OrderedDict()
        self.cache=OrderedDict()
        self.lock=threading.RLock()
        self.scope=''

    def reference(self, source: str, scope: str, name: str = '', image: bool = False):
        if not isinstance(source,str) or not source or len(source)>35_000_000:return None
        source=source.removeprefix('file://')
        if source.startswith('/') and len(source)>3 and source[2:4]==':/':source=source[1:]
        inline=source.startswith('data:image/')
        if not inline:
            path=Path(source)
            if not path.is_absolute():return None
            # References never expose credential stores, executable content or a
            # directory browser. Only a message's exact registered file is read.
            if any(part.casefold() in ('.ssh','.aws','.azure') for part in path.parts):return None
            if path.name.casefold() in ('auth.json','access_key.txt','desktop_control.dat','paired_devices.json'):return None
            if path.suffix.lower() in ('.exe','.dll','.key','.pem','.pfx','.sqlite','.db'):return None
        ident=hashlib.sha256(self.key+scope.encode()+source.encode()).hexdigest()[:32]
        label=(name or ('图片' if inline else ntpath.basename(source)))[:180]
        kind='image' if image or inline or Path(source).suffix.lower() in ('.png','.jpg','.jpeg','.webp','.gif','.bmp','.avif','.heic','.heif','.svg') else 'file'
        with self.lock:
            self.items[ident]=(scope,source,label,kind)
            self.items.move_to_end(ident)
            while len(self.items)>512:self.items.popitem(last=False)
        return {'id':ident,'name':label,'kind':kind}

    def read(self, ident: str, variant: str = 'original'):
        if variant not in ('original','thumb'):raise ValueError('预览类型无效')
        with self.lock:
            item=self.items.get(ident)
            if not item or item[0]!=self.scope:raise ValueError('附件不属于当前对话，请重新打开')
            scope,source,name,kind=item
        if source.startswith('data:image/'):
            prefix,encoded=source.split(',',1)
            if ';base64' not in prefix:raise ValueError('此图片暂不能预览')
            data=base64.b64decode(encoded,validate=True);stamp=ident
        else:
            path=Path(source)
            if not path.is_file() or path.stat().st_size>25*1024*1024:raise ValueError('原文件已不存在或超过预览大小')
            stamp=str(path.stat().st_mtime_ns)+':'+str(path.stat().st_size)
            data=None
        cache_key=(ident,variant,stamp)
        with self.lock:
            if cache_key in self.cache:
                self.cache.move_to_end(cache_key);return self.cache[cache_key]
        if data is None:data=path.read_bytes()
        if kind=='image':
            from PIL import Image,ImageOps
            with Image.open(io.BytesIO(data)) as original:
                if original.width*original.height>60_000_000:raise ValueError('图片尺寸超过预览范围')
                picture=ImageOps.exif_transpose(original)
                picture.thumbnail((360,360) if variant=='thumb' else (4096,4096))
                output=io.BytesIO();picture.convert('RGB').save(output,'JPEG',quality=76 if variant=='thumb' else 90)
                data=output.getvalue();mime='image/jpeg'
        else:
            mime=mimetypes.guess_type(name)[0] or 'application/octet-stream'
            if mime!='application/pdf':
                if len(data)>1024*1024:raise ValueError('文件过大，暂不提供文字预览')
                try:data=data.decode('utf-8-sig').encode('utf-8');mime='text/plain; charset=utf-8'
                except UnicodeError:raise ValueError('此文件格式暂不支持预览') from None
        result=(data,mime,name,hashlib.sha256(data).hexdigest())
        with self.lock:
            self.cache[cache_key]=result
            while len(self.cache)>64 or sum(len(v[0]) for v in self.cache.values())>24*1024*1024:self.cache.popitem(last=False)
        return result
