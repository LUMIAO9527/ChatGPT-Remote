/* Offline review fixture only. Never imported by the Windows/mobile runtime. */
(() => {
  'use strict';
  const config = window.RM_REVIEW_CONFIG;
  const report = (message) => parent.postMessage({type:'rm-review', message}, '*');
  window.addEventListener('error', e => report('预览错误：' + e.message));
  window.addEventListener('unhandledrejection', e => report('预览错误：' + (e.reason?.message || e.reason)));
  const originalMatch = window.matchMedia.bind(window);
  // about:srcdoc has no application pathname. Preserve its history entries
  // while omitting URLs; production history behavior is unchanged.
  for (const name of ['pushState','replaceState']) {
    const original = history[name].bind(history);
    history[name] = (data, title) => original(data, title);
  }
  window.matchMedia = query => {
    if (!/prefers-color-scheme/.test(query)) return originalMatch(query);
    return {matches: config.theme === 'dark', media:query, onchange:null,
      addListener(){},removeListener(){},addEventListener(){},removeEventListener(){},dispatchEvent(){return true;}};
  };
  const memory = new Map();
  if (!['pair','install'].includes(config.scenario)) {
    memory.set('chatgpt-harmony-demo-token','review-only-token');
    memory.set('chatgpt-remote-phone-surface',JSON.stringify({autoResume:config.scenario!=='home',lastSurface:config.scenario==='home'?'home':'remote',view:'chat'}));
  }
  Object.defineProperty(window,'localStorage',{value:{getItem:k=>memory.get(k)||null,
    setItem:(k,v)=>memory.set(k,String(v)),removeItem:k=>memory.delete(k),clear:()=>memory.clear()}});
  window.WebSocket = undefined; // Deliberately exercise the real HTTP fallback UI.
  const model = {
    epoch:1, title:'周末短途计划', key:'review-chat-1', menu:'', model:config.modelCount===1?'默认模型':'示例模型 A', effort:3, quick:false,
    generating:config.scenario === 'queue', paused:config.scenario === 'paused',approval:config.scenario === 'approval',
    offline:config.scenario === 'offline', draft:config.scenario === 'draft' ? '先把交通方式和预算列出来。' : '',
    messages:[
      {id:'m1',role:'user',text:'周末去杭州，两天，想轻松一点。',timestamp:'10:24'},
      {id:'m2',role:'assistant',text:'可以，两天都住在西湖附近，少花时间在路上。\n\n周六 · 西湖慢走\n上午抵达后放好行李，下午沿湖散步。傍晚找一家小店吃饭。\n\n周日 · 留点余地\n去龙井喝茶，午后返程。下雨就换成博物馆。',complete:true,timestamp:'10:25'}
    ], queue:[], revision:1, receipts:new Map(), uploads:new Map(), uploadSequence:0,
    devices:[{id:'demo-mate',name:'我的 Mate80',created:1793923200}],
    preferences:{theme:config.theme,material:'solid',motion:'reduced',autoHide:true}, autoStart:true,
    update:{status:'idle',installable:true,message:'检查是否有新版本'}, running:config.scenario !== 'offline',targetPaused:false
  };
  if (/^(queue|paused)$/.test(config.scenario)) {
    model.queue = [{id:'q1',text:'交通优先选高铁，住在老城区附近。',attachments:[]},
      {id:'q2',text:'再补一份下雨时可以替换的室内行程，不要安排太满。',attachments:[{name:'行程参考.pdf',kind:'file'}]}];
  }
  if (config.scenario === 'queue') {
    model.messages.push({id:'work',role:'assistant',phase:'commentary',text:'正在核对行程时间和换乘距离。',
      blocks:[{kind:'paragraph',text:'正在核对行程时间和换乘距离。',channel:'commentary'}],complete:false,work:{status:'working'}});
  }
  if (config.scenario === 'long') {
    for (let i=0;i<10;i++) model.messages.push({id:'long-'+i,role:i%2?'assistant':'user',complete:true,
      text:i%2?'把计划拆成可以随时调整的小步骤。\n\n• 先确定必须完成的事项。\n• 为交通和休息留出余量。\n• 碰到变化时保留未完成内容。\n\n示例代码\nconst itinerary = { days: 2, flexible: true };':'继续细化第 '+(i+1)+' 项，给我具体说明。'});
  }
  const clone = value => JSON.parse(JSON.stringify(value));
  const approval = () => ({available:true,epoch:model.epoch,key:'approval-fixture',menu:false,category:'prompt',
    prompt:{title:'批准运行命令',text:'读取示例项目目录以核对文件列表。\nGet-ChildItem -Name',approval:true,truncated:false},
    items:[{id:'allow-once',title:'允许一次',kind:'option',enabled:true},{id:'deny',title:'拒绝',kind:'option',enabled:true}]});
  const modelNames = config.modelNames || ['默认模型','示例模型 A','示例模型 B','示例模型 C','示例模型 D','示例模型 E','示例模型 F','示例模型 G','示例模型 H'];
  const models = modelNames.slice(0,config.modelCount || (config.scenario==='chat'?3:modelNames.length));
  const levels = ['低','标准','高','超高','Max','Ultra'];
  const options = () => {
    const items = model.menu==='models' ? models.map((title,i)=>({id:'choice-'+i,title,selected:title===model.model,kind:'option'})) :
      model.menu==='model' ? [
        {id:'model-list',title:'选择模型',kind:'model-list'},
        {id:'quick',title:model.quick?'关闭快速模式':'启用快速模式',kind:'quick',selected:model.quick},
        {id:'reset',title:'重置为默认',kind:'reset'},
        ...levels.map((label,i)=>({id:'effort-'+(i+1),title:String(i+1),label,kind:'effort-step',
          level:i+1,currentLevel:model.effort,levelCount:levels.length,selected:i+1===model.effort}))
      ] : [{id:'model',title:model.model,kind:'model'}];
    return {available:true,epoch:model.epoch,scope:model.key,key:'opts-'+model.revision,menu:!!model.menu,
      category:model.menu?'model':'',items:items.map(item=>({enabled:true,selected:false,...item}))};
  };
  const queue = () => ({available:true,key:'queue-'+model.revision,epoch:model.epoch,paused:model.paused,hasDraft:!!model.draft,
    items:model.queue.map(row=>({...row,actions:['steer','delete','edit'].map(operation=>({id:row.id+'|'+operation,operation,enabled:true}))})),
    actions:[...(model.paused?[{id:'resume',operation:'resume',enabled:true}]:[]),...(model.generating?[{id:'stop',operation:'stop',enabled:true}]:[])]});
  const draft = () => ({ok:true,conversationKey:model.key,epoch:model.epoch,revision:JSON.stringify([model.key,model.draft]),title:model.title,
    text:model.draft,hasDraft:!!model.draft,truncated:false,canClear:true,attachments:[]});
  const windows = () => ({ok:true,version:config.version,instance:'offline-review',mock:true,computerName:'我的主电脑',
    mode:'window',status:'ready',epoch:model.epoch,frozen:{active:false,reason:'',remaining:0},
    capabilities:{scope:'chatgpt-window',desktop:false,rawInput:false,windowView:true,conversation:true,nativeQueue:true,composerOptions:true},
    windows:[{id:1,title:'ChatGPT · '+model.title,exe:'ChatGPT.exe',selected:true}]});
  const snapshot = () => ({...windows(),available:true,key:model.key,title:model.title,messages:clone(model.messages),
    order:model.messages.map(m=>m.id),kind:'conversation',replace:true,revision:'rev-'+model.revision,
    generating:model.generating,queue:queue(),queued:clone(model.queue),loadedOnly:false,truncated:false,notice:'',
    composer:{available:true,hasDraft:!!model.draft,options:options()},...(model.approval?{prompt:approval()}:{})});
  const navigation = () => ({ok:true,available:true,key:'navigation',epoch:model.epoch,source:'offline-review',notice:'',items:[
    {id:'project',kind:'project',title:'日常计划',project:'daily',projectTitle:'日常计划',expanded:true},
    {id:'new-global',kind:'new',title:'新对话',project:'',projectTitle:''},
    {id:'main',kind:'thread',title:'周末短途计划',project:'daily',projectTitle:'日常计划'},
    {id:'other',kind:'thread',title:'下周的阅读清单',project:'daily',projectTitle:'日常计划'}]});
  const streams = new Set();
  function broadcast(){for(const push of streams)push();}
  function mutate(){model.revision++;broadcast();}
  const json = (data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
  function action(data) {
    if(data.epoch!==model.epoch)return {ok:false,error:'对话已切换，请重新确认。'};
    let result={ok:true,epoch:model.epoch,processingMs:0,actionSequence:model.revision};
    if(data.kind==='message') {
      if(data.messageId && model.receipts.has(data.messageId)) return {...result,delivery:model.receipts.get(data.messageId)};
      const queued=model.generating||model.paused;
      const draftChanged=data.draftRevision && data.draftRevision!==draft().revision;
      if(draftChanged || (model.draft && !data.draftRevision && data.text!==model.draft)){
        const rejection=draftChanged?{error:'电脑草稿已变化。',code:'draft_changed'}:{error:'电脑输入框已有草稿。',code:'draft_exists'};
        if(!data.messageId)return {ok:false,...rejection};
        const receipt={id:data.messageId,state:'failed',instance:'offline-review',conversationKey:model.key,epoch:model.epoch,...rejection};
        model.receipts.set(data.messageId,receipt);
        return {...result,delivery:receipt};
      }
      model.draft='';
      const attachments=(data.attachmentIds||[]).map(id=>model.uploads.get(id)).filter(Boolean);
      if(queued)model.queue.push({id:'q'+Date.now(),text:data.text,attachments});
      else {
        model.messages.push({id:'u'+Date.now(),role:'user',text:data.text,attachments,complete:true});
        model.messages.push({id:'a'+Date.now(),role:'assistant',text:'已收到。这是用于检查交互的模拟回复；真实运行时，这里显示电脑当前 ChatGPT 对话的内容。',complete:true});
      }
      const receipt={id:data.messageId,state:queued?'queued':'sent',instance:'offline-review',conversationKey:model.key,epoch:model.epoch};
      if(data.messageId)model.receipts.set(data.messageId,receipt);
      result={...result,submission:{state:receipt.state},...(data.messageId?{delivery:receipt}:{})};
      report(queued?'模拟消息已排队':'模拟消息已发送');
    } else if(data.kind==='queue') {
      if(data.targetId==='resume'){model.paused=false;model.generating=true;}
      else if(data.targetId==='stop'){model.paused=true;model.generating=false;}
      else {
        const [id,op]=data.targetId.split('|');const row=model.queue.find(r=>r.id===id);
        if(!row)return {ok:false,error:'队列已变化。'};
        if(op==='edit' && model.draft)return {ok:false,error:'电脑输入框已有草稿。',code:'draft_exists'};
        model.queue=model.queue.filter(r=>r.id!==id);
        if(op==='edit')model.draft=row.text;
        if(op==='steer')model.messages.push({id:'steer'+Date.now(),role:'user',text:row.text,complete:true});
        if(op==='edit')result.queueDraft=draft();
      }
      model.revision++;result.queue=queue();report('模拟队列已更新');
    } else if(data.kind==='composer') {
      if(model.approval){
        if(data.operation!=='invoke' || data.optionsKey!==approval().key || !approval().items.some(item=>item.id===data.targetId))return {ok:false,error:'电脑审批已变化。'};
        model.approval=false;mutate();report('模拟审批已回读');
        return {...result,options:options()};
      }
      const current=options();
      if(data.optionsKey!==current.key)return {ok:false,error:'模拟选项已变化'};
      if(data.operation==='dismiss')model.menu='';
      else{
        const choice=current.items.find(item=>item.id===data.targetId);
        if(!choice)return {ok:false,error:'模拟选项不存在'};
        if(choice.kind==='model')model.menu='model';
        if(choice.kind==='model-list')model.menu='models';
        if(choice.kind==='option'){model.model=choice.title;model.menu='';}
        if(choice.kind==='effort-step')model.effort=choice.level;
        if(choice.kind==='quick')model.quick=!model.quick;
        if(choice.kind==='reset'){model.model=models[0];model.effort=3;model.quick=false;}
      }
      mutate();report('模拟选项已回读');
      return {...result,options:options()};
    } else if(data.kind==='navigate') {
      if(data.targetId!=='project'){
        model.epoch++;model.key='review-chat-'+model.epoch;model.title=data.targetId==='other'?'下周的阅读清单':data.targetId==='main'?'周末短途计划':'新对话';
        model.messages=[{id:'hello-'+model.epoch,role:'assistant',text:'这是一段合成示例对话。可以输入文字、切换模型，或返回项目列表。',complete:true}];
        model.queue=[];model.paused=false;model.generating=false;model.draft='';
      }
      result.epoch=model.epoch;result.navigation=navigation();
    } else if(data.kind==='draft')model.draft='';
    mutate();return result;
  }
  window.fetch = async (path, init={}) => {
    const url=new URL(String(path),'https://preview.invalid');
    if(model.offline && url.pathname!=='/api/pair')throw new TypeError('offline review scenario');
    if(init.signal?.aborted)throw new DOMException('Aborted','AbortError');
    const data=typeof init.body==='string'?JSON.parse(init.body):{};
    switch(url.pathname) {
      case '/api/health':return json(windows());
      case '/api/pair':return data.pin==='246810'?json({ok:true,token:'review-only-token'}):json({ok:false,error:'示例配对码是 246810。'},403);
      case '/api/windows': case '/api/select':case '/api/window':return json(windows());
      case '/api/conversation':return json(snapshot());
      case '/api/conversation/stream': {
        let push,interval,controller;
        const clear=()=>{streams.delete(push);clearInterval(interval);};
        const body=new ReadableStream({start(c){controller=c;push=()=>{try{c.enqueue(new TextEncoder().encode(JSON.stringify(snapshot())+'\n'));}catch{clear();}};streams.add(push);push();interval=setInterval(push,6000);
          init.signal?.addEventListener('abort',()=>{clear();try{c.error(new DOMException('Aborted','AbortError'));}catch{}},{once:true});},cancel:clear});
        return new Response(body,{headers:{'Content-Type':'application/x-ndjson'}});
      }
      case '/api/queue':return json({ok:true,...queue()});
      case '/api/navigation':return config.scenario==='navigation-error' ? json({ok:false,error:'项目列表读取失败，请稍后重试。'},503) : json(navigation());
      case '/api/composer':return json({ok:true,...(model.approval?approval():options())});
      case '/api/draft':return json(draft());
      case '/api/action': {const result=action(data);return json(result,result.ok?200:409);}
      case '/api/delivery':return model.receipts.has(url.searchParams.get('id'))?json({ok:true,delivery:model.receipts.get(url.searchParams.get('id'))}):json({ok:false,error:'示例发送记录不存在'},404);
      case '/api/unpair':return json({ok:true});
      case '/api/stream':return json({ok:false,error:'离线评审使用静态模拟画面'},415);
      case '/api/frame': {
        await new Promise(r=>setTimeout(r,250));
        const bytes=Uint8Array.from(atob(config.frame),c=>c.charCodeAt(0));
        return new Response(bytes,{headers:{'Content-Type':'image/jpeg','X-Frame-Id':String(Date.now()),'X-Target-Epoch':String(model.epoch),'X-Captured-Ms':String(Date.now()),'X-Capture-Method':'offline-review','X-Action-Sequence':String(model.revision)}});
      }
      case '/api/uploads': {
        const name=decodeURIComponent(init.headers['X-File-Name']||'示例附件');
        const file={id:'file-'+(++model.uploadSequence),name,state:'uploaded',size:init.body?.size||0};
        model.uploads.set(file.id,{id:file.id,name,kind:/\.(png|jpe?g|webp|gif|heic)$/i.test(name)?'image':'file'});
        return json({ok:true,file});
      }
      case '/api/uploads/attach':case '/api/uploads/status':return json({ok:true,state:'attached',files:[]});
      case '/api/uploads/discard':case '/api/stream/ack':return json({ok:true});
      case '/api/freeze':return json({...windows(),frozen:{active:!!data.frozen,reason:'manual',remaining:0}});
      default:return json({ok:false,error:'此离线演示未模拟该操作：'+url.pathname},400);
    }
  };
  let updateTimer;
  function desktopStatus(){return {ok:true,version:config.version,computerName:'我的主电脑',running:model.running,
    phoneUrl:model.running?'https://demo.invalid/rm/':'',windows:windows().windows,target:windows().windows[0],targetStatus:'ready',mode:'window',paused:model.targetPaused,
    devices:clone(model.devices),preferences:clone(model.preferences),update:clone(model.update),
    installation:{firstRun:config.scenario==='install',sourceMode:false,suggested:'D:\\ChatGPTRemote',current:'D:\\ChatGPTRemote',installed:true}};}
  window.pywebview={api:{call:async(actionName,data={})=>{
    if(actionName==='status')return desktopStatus();
    if(actionName==='pair')return {ok:true,pin:'246810',expiresAt:Date.now()/1000+900,url:'https://demo.invalid/rm/',qr:config.qr};
    if(actionName==='settings.get')return {ok:true,preferences:clone(model.preferences),autoStart:model.autoStart};
    if(actionName==='settings.autostart'){model.autoStart=data.enabled;return {ok:true,autoStart:model.autoStart};}
    if(actionName.startsWith('settings.')){Object.assign(model.preferences,data);return {ok:true,preferences:clone(model.preferences)};}
    if(actionName==='device.rename'){const d=model.devices.find(d=>d.id===data.id);if(d)d.name=data.name;}
    if(actionName==='device.remove')model.devices=model.devices.filter(d=>d.id!==data.id);
    if(actionName==='service.start'||actionName==='service.restart')model.running=true;
    if(actionName==='service.stop')model.running=false;
    if(actionName==='target.pause')model.targetPaused=!!data.paused;
    if(actionName==='updates.status')return {ok:true,update:clone(model.update)};
    if(actionName==='updates.check'){
      clearTimeout(updateTimer);
      model.update={status:'checking',current:config.version,installable:true};
      const [major,minor,patch=0]=config.version.split('.').map(Number);
      updateTimer=setTimeout(()=>{
        model.update={...model.update,status:'available',available:true,version:[major,minor,patch+1].join('.'),
          notes:'版本更新页将版本号与更新说明合并。\n示例下载和安装仅供演示。'};
      },450);
    }
    if(actionName==='updates.download'){clearTimeout(updateTimer);model.update={...model.update,status:'downloading',progress:36,message:'正在下载（模拟）'};updateTimer=setTimeout(()=>{model.update={...model.update,status:'ready',progress:100,message:'下载完成，已校验（模拟）'};},2000);}
    if(actionName==='updates.cancel'){clearTimeout(updateTimer);model.update={...model.update,status:'error',message:'已取消模拟下载'};}
    if(actionName==='updates.install'){clearTimeout(updateTimer);model.update={status:'idle',installable:true,message:'模拟更新已结束；没有安装或重启任何程序'};report('仅演示更新，不会安装或重启');}
    if(actionName==='installation.choose')return {ok:true,path:'D:\\ChatGPTRemote'};
    if(actionName==='installation.install'||actionName==='uninstall')report('仅演示 '+actionName+'，没有修改电脑');
    if(['link.copy','support.copy','diagnostics.copy'].includes(actionName))report('复制操作为演示；没有真实连接凭据');
    if(actionName==='links.open')report('评审页不打开外部链接');
    if(actionName==='window.close')report('预览保留在此页面，未关闭任何程序');
    return {...desktopStatus(),ok:true};
  }}};
})();
