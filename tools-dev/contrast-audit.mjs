import { chromium } from "playwright";
const URL = process.argv[2] || "http://localhost:3000/";
const SCHEME = process.argv[3] || "light";
const FN = `
function _lum(r,g,b){const f=c=>{c/=255;return c<=0.03928?c/12.92:Math.pow((c+0.055)/1.055,2.4)};return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b)}
function _pc(s){const m=String(s).match(/rgba?\\(([^)]+)\\)/);if(!m)return null;const p=m[1].split(',').map(x=>parseFloat(x));return{r:p[0],g:p[1],b:p[2],a:p[3]===undefined?1:p[3]}}
function _comp(fg,bg){const a=fg.a;return{r:fg.r*a+bg.r*(1-a),g:fg.g*a+bg.g*(1-a),b:fg.b*a+bg.b*(1-a),a:1}}
function _ebg(el){let e=el;while(e&&e!==document.documentElement){const cs=getComputedStyle(e);if(cs.backgroundImage&&cs.backgroundImage!=='none')return{grad:1};const c=_pc(cs.backgroundColor);if(c&&c.a>0){if(c.a>=0.999)return c;return _comp(c,_ebg(e.parentElement))}e=e.parentElement}return{r:255,g:255,b:255,a:1}}
function _con(el){const cs=getComputedStyle(el);const fg=_pc(cs.color);if(!fg)return null;const bg=_ebg(el);if(bg.grad)return null;const fc=_comp(fg,bg);const L1=_lum(fc.r,fc.g,fc.b)+0.05,L2=_lum(bg.r,bg.g,bg.b)+0.05;const ratio=Math.max(L1,L2)/Math.min(L1,L2);const size=parseFloat(cs.fontSize);const bold=parseInt(cs.fontWeight)>=700;const large=size>=24||(size>=18.66&&bold);const white=fg.r>240&&fg.g>240&&fg.b>240;return{ratio:Math.round(ratio*100)/100,thr:large?3:4.5,fg:cs.color,white}}
function _ht(el){for(const n of el.childNodes)if(n.nodeType===3&&n.textContent.trim().length>0)return true;return false}
function _vis(el){const r=el.getBoundingClientRect();const cs=getComputedStyle(el);return r.width>2&&r.height>2&&cs.visibility!=='hidden'&&parseFloat(cs.opacity)>0.1}
window.__audit=function(){const out=[];for(const cell of document.querySelectorAll('[data-block-cell]')){for(const el of cell.querySelectorAll('*')){if(!_ht(el)||!_vis(el))continue;const c=_con(el);if(c&&c.ratio<c.thr&&!c.white)out.push({block:cell.dataset.blockType,ratio:c.ratio,need:c.thr,fg:c.fg,txt:(el.textContent||'').trim().slice(0,24)})}}return out.sort((a,b)=>a.ratio-b.ratio)}
`;
const b = await chromium.launch();
const ctx = await b.newContext({ viewport:{width:1920,height:1080}, colorScheme: SCHEME });
await ctx.addInitScript(FN);
const p = await ctx.newPage();
await p.goto(URL, { waitUntil:"domcontentloaded" });
await p.waitForTimeout(7000);
const res = await p.evaluate(()=>window.__audit());
console.log(`Контраст-аудит ${URL} (${SCHEME}): ${res.length} нарушений (бело-на-overlay исключены)`);
for (const r of res) console.log(`  ${r.ratio} (нужно ${r.need}) — ${r.block}: "${r.txt}" fg=${r.fg}`);
await b.close();
