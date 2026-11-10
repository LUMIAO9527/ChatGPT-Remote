/* Motion scale and interruption behavior adapted from ChatGPTnx v1.6.0. */
window.RemoteMotion=(()=>{
 const reduce=matchMedia('(prefers-reduced-motion: reduce)');let enabled=null,openEpoch=0;
 const active=new Map(),ease='cubic-bezier(.2,.75,.25,1)';
 const allowed=node=>enabled!==false&&(enabled===true||!reduce.matches)&&typeof node?.animate==='function';
 function stop(){
  openEpoch++;
  const records=[...active.values()];active.clear();
  for(const {animation,settle} of records){animation.cancel();settle?.();}
 }
 function play(node,frames,duration,settle){
  const prior=active.get(node);prior?.animation.cancel();active.delete(node);
  if(!allowed(node)){settle?.();return null;}
  const animation=node.animate(frames,{duration,easing:ease,fill:'both'}),record={animation,settle};
  active.set(node,record);
  animation.onfinish=()=>{
   if(active.get(node)!==record)return;
   active.delete(node);animation.cancel();settle?.();
  };
  return animation;
 }
 function open(node){
  const ticket=++openEpoch;
  const start=()=>{
   if(ticket!==openEpoch||!allowed(node))return;
   const prior=active.has(node),style=prior?getComputedStyle(node):null;
   play(node,[{opacity:prior?Number(style.opacity):0,transform:prior?style.transform:'translateY(8px) scale(.985)',transformOrigin:'center bottom'},
    {opacity:1,transform:'none',transformOrigin:'center bottom'}],260);
  };
  if(typeof requestAnimationFrame==='function')requestAnimationFrame(()=>requestAnimationFrame(start));else start();
 }
 function enter(node,direction=1){
  if(!allowed(node))return;
  const prior=active.has(node),from=prior?Number(getComputedStyle(node).opacity):0;
  const transform=prior?getComputedStyle(node).transform:'translateX('+(direction>0?10:-10)+'px)';
  play(node,[{opacity:from,transform},{opacity:1,transform:'none'}],220);
 }
 function content(node){
  if(!allowed(node))return;
  play(node,[{opacity:.3},{opacity:1}],180);
 }
 function pop(node,origin='top right'){
  if(!node)return;
  node.hidden=false;node.style.pointerEvents='';
  if(!allowed(node))return;
  const prior=active.has(node),style=prior?getComputedStyle(node):null;
  play(node,[{opacity:prior?Number(style.opacity):0,transform:prior?style.transform:'translateY(4px) scale(.96)',transformOrigin:origin},
   {opacity:1,transform:'none',transformOrigin:origin}],170);
 }
 function dismiss(node,done){
  if(!node){done?.();return;}
  node.style.pointerEvents='none';
  const settle=()=>{node.style.pointerEvents='';done?.();};
  if(!allowed(node)){settle();return;}
  play(node,[{opacity:Number(getComputedStyle(node).opacity),transform:getComputedStyle(node).transform},
   {opacity:0,transform:'translateY(3px) scale(.98)'}],120,settle);
 }
 function reveal(node,open){
  const hidden=node.hidden,current=node.getBoundingClientRect().height,opacity=hidden?0:allowed(node)?Number(getComputedStyle(node).opacity):1;
  active.get(node)?.animation.cancel();active.delete(node);node.hidden=false;
  node.style.height='auto';const target=open?node.getBoundingClientRect().height:0;
  play(node,[{height:current+'px',opacity},{height:target+'px',opacity:open?1:0}],open?240:180,()=>{node.hidden=!open;node.style.height='';});
 }
 function emphasize(node){
  if(!allowed(node))return;
  play(node,[{opacity:1},{opacity:.45},{opacity:1}],420);
 }
 reduce.addEventListener('change',()=>{if(enabled!==true)stop();});
 return {open,enter,content,pop,dismiss,reveal,emphasize,stop,setEnabled(value){enabled=typeof value==='boolean'?value:null;if(enabled===false||enabled===null&&reduce.matches)stop();}};
})();
