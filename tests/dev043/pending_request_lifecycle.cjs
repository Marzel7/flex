const fs=require('fs'),vm=require('vm'),assert=require('assert');
const template=fs.readFileSync(require('path').join(__dirname,'../../templates/treasury_review.html'),'utf8');
const script=template.match(/<script>([\s\S]*?)<\/script>/)[1];
function harness(){
 const elements={},events={},timers=new Map(),calls=[];let serial=0;
 for(const id of ['tr-list','tr-more','tr-sort','tr-status','tr-summary'])elements[id]={value:id==='tr-sort'?'actionable':'PENDING_REVIEW',textContent:'',innerHTML:'',style:{},attrs:{},listeners:{},setAttribute(k,v){this.attrs[k]=v},addEventListener(k,v){this.listeners[k]=v},insertAdjacentHTML(_,v){this.innerHTML+=v}};
 const context={document:{getElementById:id=>elements[id]},window:{addEventListener:(k,v)=>events[k]=v},AbortController,Date,Promise,encodeURIComponent,setTimeout:(fn,ms)=>{assert.equal(ms,3000);timers.set(++serial,fn);return serial},clearTimeout:id=>timers.delete(id),fetch:(url,options)=>new Promise((resolve,reject)=>calls.push({url,options,resolve,reject}))};
 vm.runInNewContext(script,context);return{elements,events,timers,calls,expire(){[...timers.values()].forEach(fn=>fn())},filter(v){elements['tr-status'].value=v;elements['tr-status'].listeners.change()}};
}
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve()};
const payload=n=>({ok:true,pending_total:n,items:[],returned:0,has_more:false});
const respond=(call,n)=>call.resolve({ok:true,status:200,json:async()=>payload(n)});
(async()=>{
 let h=harness();respond(h.calls[0],7);await flush();assert(h.elements['tr-summary'].innerHTML.includes('7'));assert.equal(h.elements['tr-list'].attrs['aria-busy'],'false');assert.equal(h.timers.size,0);
 h=harness();h.expire();await flush();assert(h.calls[0].options.signal.aborted);assert(h.elements['tr-list'].textContent.includes('UNAVAILABLE'));assert(h.elements['tr-list'].textContent.includes('3 seconds'));assert.equal(h.elements['tr-list'].attrs['aria-busy'],'false');assert.equal(h.calls.length,1);respond(h.calls[0],999);await flush();assert(!h.elements['tr-summary'].innerHTML.includes('999'));
 for(const error of [new Error('network down'),Object.assign(new Error('Aborted'),{name:'AbortError'})]){h=harness();h.calls[0].reject(error);await flush();assert(h.elements['tr-list'].textContent.includes('UNAVAILABLE'));assert.equal(h.timers.size,0)}
 h=harness();h.calls[0].resolve({ok:false,status:503});await flush();assert(h.elements['tr-list'].textContent.includes('HTTP 503'));
 h=harness();h.calls[0].resolve({ok:true,json:()=>new Promise(()=>{})});await flush();h.expire();await flush();assert(h.elements['tr-list'].textContent.includes('UNAVAILABLE'));
 h=harness();h.filter('CONFIRMED');assert.equal(h.calls.length,2);assert(h.calls[0].options.signal.aborted);respond(h.calls[1],11);await flush();respond(h.calls[0],99);await flush();assert(h.elements['tr-summary'].innerHTML.includes('11'));assert(!h.elements['tr-summary'].innerHTML.includes('99'));assert(h.calls[1].url.includes('status=CONFIRMED'));assert(h.calls[1].url.endsWith('limit=20&offset=0'));
 h=harness();h.events.pagehide();await flush();assert(h.calls[0].options.signal.aborted);respond(h.calls[0],99);await flush();assert.equal(h.elements['tr-summary'].innerHTML,'');assert.equal(h.timers.size,0);h.events.pagehide();assert.equal(h.calls.length,1);
 console.log('PASS: fast,3s timeout,network,503,abort,hung body,stale filter,pagehide/repeated navigation,no automatic retries; actual inline script executed');
})().catch(e=>{console.error(e);process.exitCode=1});
