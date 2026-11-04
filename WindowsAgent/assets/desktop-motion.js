/* Motion scale and interruption behavior adapted from ChatGPTnx v1.6.0. */
window.RemoteMotion=(()=>{
 const reduce=matchMedia('(prefers-reduced-motion: reduce)');let enabled=true;
 const active=new Map();
 const allowed=node=>enabled&&!reduce.matches&&typeof node?.animate==='function';
 function stop(){for(const animation of active.values())animation.cancel();active.clear();}
 function enter(node,direction=1){
  if(!allowed(node))return;
  const prior=active.get(node),from=prior?parseFloat(getComputedStyle(node).opacity):0;
  const transform=prior?getComputedStyle(node).transform:'translateY('+(direction>0?4:-3)+'px)';
  prior?.cancel();
  const animation=node.animate([{opacity:Number.isFinite(from)?from:0,transform},{opacity:1,transform:'translateY(0)'}],
   {duration:240,easing:'cubic-bezier(.2,.7,.25,1)',fill:'both'});
  active.set(node,animation);animation.onfinish=()=>{if(active.get(node)===animation)active.delete(node);animation.cancel();};
 }
 function reveal(node,open){
  const current=node.getBoundingClientRect().height;active.get(node)?.cancel();node.hidden=false;
  node.style.height='auto';const target=open?node.getBoundingClientRect().height:0;
  if(!allowed(node)){node.hidden=!open;return;}
  const animation=node.animate([{height:current+'px',opacity:open?0:1},{height:target+'px',opacity:open?1:0}],
   {duration:300,easing:'cubic-bezier(.2,.7,.25,1)',fill:'both'});
  active.set(node,animation);animation.onfinish=()=>{if(active.get(node)!==animation)return;active.delete(node);node.hidden=!open;animation.cancel();};
 }
 reduce.addEventListener('change',stop);
 return {enter,reveal,stop,setEnabled(value){enabled=value!==false;if(!enabled)stop();}};
})();
