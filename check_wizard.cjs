const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map();
function make(id,tag='div',attrs=''){
 let value=attrs.match(/value="([^"]*)"/)?.[1]??'',html='';
 const classes=new Set((attrs.match(/class="([^"]*)"/)?.[1]??'').split(/\s+/));
 const obj={id,tag,disabled:false,checked:false,files:[],get value(){return value},set value(x){value=String(x)},classList:{s:classes,add(x){classes.add(x)},remove(x){classes.delete(x)},toggle(x,v){if(v===undefined)v=!classes.has(x);v?classes.add(x):classes.delete(x)}},textContent:'',get innerHTML(){return html},set innerHTML(x){html=x;scan(x);if(tag==='select'){const options=[...html.matchAll(/<option value="([^"]*)"/g)].map(m=>m[1]);if(!options.includes(value))value=options[0]??'';}},get selectedOptions(){const options=[...html.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)];return options.filter(m=>m[1]===value).map(m=>({value:m[1],textContent:m[2]}));},setAttribute(){},querySelectorAll(){return []},events:{},addEventListener(k,f){(this.events[k]??=[]).push(f)},closest(){return this.label??=make(id+'-label')},appendChild(){},insertAdjacentHTML(where,html){scan(html)},focus(){},remove(){elements.delete(id);const match=id.match(/^source-row-(\d+)$/);if(match)for(const key of [...elements.keys()])if(key.startsWith('s'+match[1]+'_'))elements.delete(key)},querySelector(){return make(id+'-h2')},scrollIntoView(){}};
 return obj;
}
function scan(html){for(const m of html.matchAll(/<([a-z][a-z0-9]*)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){if(!elements.has(m[3]))elements.set(m[3],make(m[3],m[1],m[2]));}}
scan(fs.readFileSync(process.argv[2],'utf8').split('<script>')[0]);
const context={console,Number,Object,String,JSON,Math,Date,Set,window:{confirm:()=>true,location:{pathname:'/'},history:{pushState(state,title,path){context.window.location.pathname=path},replaceState(state,title,path){context.window.location.pathname=path}},events:{},addEventListener(k,f){this.events[k]=f}},document:{events:{},addEventListener(k,f){this.events[k]=f},getElementById:id=>elements.get(id)||null,querySelectorAll:query=>query==='input,select,textarea'?[...elements.values()].filter(e=>['input','select','textarea'].includes(e.tag)):[...elements.values()].filter(e=>e.classList.s.has(query.slice(1)))},fetch:(path,init)=>fetch(process.argv[4]+path,init)};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[3],'utf8'),context);
const get=id=>elements.get(id),state=()=>vm.runInContext('wizardStep',context),next=()=>get('wizard-next').onclick(),back=()=>get('wizard-back').onclick();
(async()=>{
 assert(!get('home-workspace').classList.s.has('hidden'));assert(get('single-workspace').classList.s.has('hidden'));assert(get('batch-workspace').classList.s.has('hidden'));
 get('choose-multiple').onclick();assert.equal(context.window.location.pathname,'/scenarios');assert(!get('batch-workspace').classList.s.has('hidden'));
 assert(!get('single-view'));assert(!get('batch-view'));
 context.window.confirm=()=>false;get('home-view').onclick();assert.equal(context.window.location.pathname,'/scenarios');assert(get('home-workspace').classList.s.has('hidden'));
 context.window.confirm=()=>true;get('home-view').onclick();assert.equal(context.window.location.pathname,'/');assert(!get('home-workspace').classList.s.has('hidden'));
 get('choose-single').onclick();assert.equal(context.window.location.pathname,'/single');assert(!get('single-workspace').classList.s.has('hidden'));
 const inputBeforeHelp=vm.runInContext('JSON.stringify(payload())',context);
 assert(vm.runInContext("prepareFieldInformation('c_service')",context));assert(get('information-title').textContent.includes('fill-rate'));
 assert(get('information-body').textContent.includes('0.95'));
 context.document.events.click({target:{closest:()=>({dataset:{helpKey:'c_yield'}})}});
 assert(get('information-body').textContent.includes('80 kg'));
 assert(vm.runInContext("infoButton('source.capacity').includes('popovertarget=\"field-information\"')",context));
 assert(vm.runInContext("sourceRow({id:4,name:'Supplier',lead:1,cp:12,ep:0,co:50,distance:0,capacity:null,investment:0}).includes('data-help-key=\"source.lead\"')",context));
 assert.equal(vm.runInContext('JSON.stringify(payload())',context),inputBeforeHelp);
 assert(!get('adi_cutoff'));assert(!get('cv2_cutoff'));
 assert.equal(state(),'start');assert(get('step-demand').classList.s.has('hidden'));
 await next();assert.equal(state(),'start');assert(get('error').innerHTML.includes('Choose Yes or No'));
 get('mode').value='known';get('mode').onchange();await next();assert.equal(state(),'demand');
 get('monthly').value='100, 200, 150';await next();assert.equal(state(),'supply');
 const originalCost=get('s2_cp').value;
 for(let j=4;j<=10;j++)get('add-source').onclick();
 assert.equal(vm.runInContext('sourceIds.length',context),10);assert(get('add-source').disabled);
 get('add-source').onclick();assert.equal(vm.runInContext('sourceIds.length',context),10);
 assert.equal(get('s2_cp').value,originalCost);
 for(let j=4;j<=10;j++){get('s'+j+'_cp').value='14';get('s'+j+'_co').value='60';}
 let p=vm.runInContext('payload()',context);assert.equal(p.sources.length,10);assert.equal(p.sources[9].id,10);
 await next();assert.equal(state(),'review');assert(get('review-content').innerHTML.includes('Supplier 10'));
 back();vm.runInContext('removeSource(6)',context);assert.equal(vm.runInContext('sourceIds.length',context),9);
 assert(!get('add-source').disabled);get('add-source').onclick();assert(get('s6_cp'));
 get('s6_cp').value='15';get('s6_co').value='70';
 // Return to three sources to keep the existing branch checks small.
 for(let j=4;j<=10;j++)vm.runInContext('removeSource('+j+')',context);
 await next();assert.equal(state(),'review');
 assert(get('review-content').innerHTML.includes('Wagner–Whitin'));
 await get('calculate').onclick();assert.equal(get('error').innerHTML,'');assert(get('results').innerHTML.includes('Deterministic order schedule'));
 back();back();assert.equal(get('monthly').value,'100, 200, 150');
 back();get('mode').value='uncertain';get('mode').onchange();await next();
 get('family').value='Normal';get('family').onchange();await next();assert.equal(state(),'classification');
 await next();assert.equal(state(),'supply');await next();assert.equal(state(),'policy');await next();assert.equal(state(),'review');
 assert(get('review-content').innerHTML.includes('Reorder Point'));
 for(const [key,value] of Object.entries({search_candidates:10,simulation_years:1,replications:2,training_replications:1,warmup_years:0}))get(key).value=value;
 await get('calculate').onclick();assert.equal(get('error').innerHTML,'');assert(get('results').innerHTML.includes('Immediate fill rate'));assert.equal(vm.runInContext('lastResult.configurations.length',context),9);back();back();back();back();assert.equal(state(),'demand');
 get('occurrence_probability').value='.3';for(const f of get('occurrence_probability').events.input)f();await next();assert.equal(state(),'classification');
 assert(get('classification').innerHTML.includes('Order-Up-To-Level'));await next();await next();await next();assert.equal(state(),'review');
 assert(get('review-content').innerHTML.includes('Bernoulli'));
 assert(!get('service-holder').classList.s.has('hidden'));
 await get('calculate').onclick();assert.equal(get('error').innerHTML,'');assert(get('results').innerHTML.includes('Order-Up-To-Level'));assert(get('results').innerHTML.includes('Review (days)'));
 vm.runInContext("for(const row of lastResult.configurations.filter(r=>r.status==='feasible'))show({...lastResult,best:row})",context);

 // Scenario snapshots must be independent of the main form and one another.
 await vm.runInContext(`(async()=>{
  scenarios=[];
  const known=payload();known.mode='known';known.monthly_demand=[100,200,150];
  scenarios.push(await checkedScenario(known,'Baseline'));
  renderScenarios();setWorkspace('batch');
 })()`,context);
 assert(!get('batch-workspace').classList.s.has('hidden'));assert.equal(context.window.location.pathname,'/scenarios');
 vm.runInContext("setWorkspace('single')",context);assert.equal(context.window.location.pathname,'/single');
 context.window.location.pathname='/scenarios';context.window.events.popstate();assert(!get('batch-workspace').classList.s.has('hidden'));
 get('monthly').value='999';assert.equal(vm.runInContext('scenarios[0].inputs.monthly_demand[0]',context),100);
 get('variation-field').value='demand_scale';get('variation-values').value='0.5, 1.5';
 await get('generate-scenarios').onclick();assert.equal(get('batch-error').innerHTML,'');
 assert.equal(vm.runInContext('scenarios.length',context),3);
 assert.equal(vm.runInContext('scenarios[1].inputs.monthly_demand[0]',context),50);
 assert.equal(vm.runInContext('scenarios[2].inputs.monthly_demand[0]',context),150);
 get('variation-values').value='1, 0';await get('generate-scenarios').onclick();
 assert.equal(vm.runInContext('scenarios.length',context),3);assert(get('batch-error').innerHTML.includes('No variations were added'));
 await vm.runInContext(`(async()=>{
  const uncertain=payload();uncertain.mode='uncertain';uncertain.demand.mu=30;uncertain.demand.sigma=0;uncertain.demand.occurrence_probability=null;
  uncertain.settings={...uncertain.settings,search_candidates:10,simulation_years:1,replications:2,training_replications:1,warmup_years:0,max_review_days:60};
  scenarios.push(await checkedScenario(uncertain,'ROP'));
  const intermittent=clone(uncertain);intermittent.demand.occurrence_probability=.3;
  scenarios.push(await checkedScenario(intermittent,'OUTL'));
  const noSupply=clone(uncertain);for(const s of noSupply.sources)s.capacity=0;
  scenarios.push(await checkedScenario(noSupply,'No annual capacity'));
  const bad=await checkedScenario(knownFallback(),'Invalid evaluation');bad.inputs.costs.yield=0;scenarios.push(bad);
  function knownFallback(){const k=clone(scenarios[0].inputs);return k;}
 })()`,context);
 await get('run-scenarios').onclick();
 assert.equal(vm.runInContext('scenarios.length',context),7);
 assert.equal(vm.runInContext('scenarios.filter(s=>s.result?.best).length',context),5);
 assert.equal(vm.runInContext('scenarios[5].result.best',context),null);
 assert.equal(vm.runInContext('scenarios[5].result.configurations.length',context),9);
 assert.equal(vm.runInContext('scenarios[6].state',context),'Evaluation failed');
 assert(get('batch-summary').innerHTML.includes('No annual capacity'));
 assert(get('batch-status').textContent.includes('7 of 7 evaluated'));
 assert(!get('export-batch-csv').disabled);
 const csv=vm.runInContext('summaryCSV()',context);assert(csv.includes('inputs_json'));assert(csv.includes('Baseline'));assert(csv.includes('Invalid evaluation'));
 const all=vm.runInContext('alternativesCSV()',context);assert(all.includes('Cp_eur_per_year'));assert(all.includes('expected annual capacity or scrap infeasible'));
 await vm.runInContext('loadScenario(scenarios[5].id,true)',context);
 assert(get('results').innerHTML.includes('No feasible solution was found'));
 assert.equal(state(),'review');
 // Stop is cooperative: the in-flight solve finishes, subsequent items remain unrun.
 await vm.runInContext(`(async()=>{
  scenarios=scenarios.slice(0,3);
  const originalRequest=request;
  request=async(path,data)=>{const r=await originalRequest(path,data);if(path==='/solve')stopRequested=true;return r;};
  await runScenarios();request=originalRequest;
 })()`,context);
 assert.equal(vm.runInContext("scenarios.filter(s=>s.state==='Not run (stopped)').length",context),2);
 assert.equal(Number(get('batch-progress').value),1);
 const preserved=vm.runInContext('JSON.stringify(scenarios[0].result)',context);
 await get('resume-scenarios').onclick();assert.equal(vm.runInContext("scenarios.filter(s=>s.state==='Completed').length",context),3);
 assert.equal(vm.runInContext('JSON.stringify(scenarios[0].result)',context),preserved);
 // Editing replaces only the named snapshot and clears its old results.
 await vm.runInContext('loadScenario(scenarios[0].id)',context);get('monthly').value='120, 200, 150';
 await get('save-scenario').onclick();assert.equal(vm.runInContext('scenarios.length',context),3);
 assert.equal(vm.runInContext('scenarios[0].inputs.monthly_demand[0]',context),120);
 assert.equal(vm.runInContext('scenarios[0].result',context),null);
 assert(vm.runInContext('scenarios[1].result!==null',context));
 // Import checks the whole file before appending anything.
 const imported=vm.runInContext("JSON.stringify({format:'powder-inventory-scenarios',schema_version:1,scenarios:[{name:'Imported',inputs:scenarios[0].inputs}]})",context);
 get('import-scenarios').files=[{size:imported.length,text:async()=>imported}];await get('import-scenarios').onchange();
 assert.equal(vm.runInContext('scenarios.length',context),4);assert.equal(get('batch-error').innerHTML,'');
 assert.equal(vm.runInContext('scenarios[3].inputs.settings.adi_cutoff',context),1.32);assert.equal(vm.runInContext('scenarios[3].inputs.settings.cv2_cutoff',context),.49);
 const badImport=JSON.parse(imported);badImport.scenarios.push({name:'Bad',inputs:{}});
 get('import-scenarios').files=[{size:100,text:async()=>JSON.stringify(badImport)}];await get('import-scenarios').onchange();
 assert.equal(vm.runInContext('scenarios.length',context),4);assert(get('batch-error').innerHTML.includes('Nothing was imported'));
 vm.runInContext("globalThis.capturedDownloads=[];downloadJSON=(data,name)=>capturedDownloads.push({data,name});",context);
 get('export-scenarios').onclick();assert.equal(vm.runInContext('capturedDownloads[0].data.schema_version',context),1);
 assert.equal(vm.runInContext('capturedDownloads[0].data.scenarios.length',context),4);
 assert.equal(vm.runInContext("csvText([['=formula','a,b','x\\\"y']]).includes(\"'=formula\")",context),true);
 await get('load-demo-scenarios').onclick();assert.equal(vm.runInContext('scenarios.length',context),10);assert.equal(get('batch-error').innerHTML,'');
 assert(vm.runInContext("fieldInformation['variation-values'].body.length>0",context));
 const formBefore=vm.runInContext('JSON.stringify(payload())',context);get('home-view').onclick();assert.equal(context.window.location.pathname,'/');assert(!get('home-workspace').classList.s.has('hidden'));
 get('choose-multiple').onclick();assert.equal(vm.runInContext('scenarios.length',context),10);get('home-view').onclick();get('choose-single').onclick();assert.equal(vm.runInContext('JSON.stringify(payload())',context),formBefore);
 context.window.confirm=()=>false;context.window.location.pathname='/';context.window.events.popstate();assert.equal(context.window.location.pathname,'/single');assert(get('home-workspace').classList.s.has('hidden'));
 context.window.confirm=()=>true;context.window.location.pathname='/';context.window.events.popstate();assert(!get('home-workspace').classList.s.has('hidden'));
 console.log('Opening page, routes, wizard and scenario analysis passed: both demand branches, snapshot isolation, variations, atomic validation/import, mixed batches, failed/no-feasible outcomes, cooperative stop, and exports');

})().catch(e=>{console.error(e);console.error('error panel',get('error').innerHTML);process.exit(1)});
