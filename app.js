/* Spartanburg Home-Buy Decision Deck */
let DATA=null, MAP=null, LAYERS={}, ACTIVE=new Set(['price','drive','schools']), BE_CHART=null, RUNWAY_CHART=null;
const fmt$=n=>n==null||isNaN(n)?'—':'$'+Math.round(n).toLocaleString();
const fmt$k=n=>n==null||isNaN(n)?'—':'$'+(Math.round(n/1000))+'K';
const pct=n=>n==null||isNaN(n)?'—':(n*100).toFixed(1)+'%';
const pct0=n=>n==null||isNaN(n)?'—':(n*100).toFixed(0)+'%';

async function boot(){
  DATA = await fetch('data.json').then(r=>r.json());
  document.getElementById('asof').textContent = DATA.global.asof;
  initMap();
  renderLayerToggles();
  renderLegend();
  renderScorecard();
  renderTiming();
  bindGlobals();
  bindRentBuy();
  bindProperty();
  bindRunway();
  document.getElementById('theme-toggle').addEventListener('click', toggleTheme);
  updateAll();
}

function toggleTheme(){
  const html=document.documentElement;
  const cur=html.getAttribute('data-theme');
  html.setAttribute('data-theme', cur==='dark'?'light':'dark');
  if(MAP){ // redraw tile brightness for theme
    setTimeout(()=>{ Object.values(LAYERS).forEach(l=>{ if(l && l.redraw) l.redraw(); }); },50);
  }
  if(BE_CHART){ BE_CHART.update(); }
  if(RUNWAY_CHART){ RUNWAY_CHART.update(); }
}

/* ---------------- MAP ---------------- */
function initMap(){
  MAP = L.map('map', {zoomControl:true, preferCanvas:true}).setView([34.955,-81.98],10);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',{
    attribution:'© OpenStreetMap contributors © CARTO', maxZoom:18, subdomains:'abcd'
  }).addTo(MAP);

  // anchor pin
  const a = DATA.global.anchor;
  const anchor = L.circleMarker([a.lat,a.lon],{radius:8,fillColor:'#e0a83a',color:'#111',weight:2,opacity:1,fillOpacity:.9}).addTo(MAP)
    .bindTooltip("Commute anchor · "+a.label,{className:'z-tip',direction:'top',offset:[0,-6]});

  // ZIP price choropleth
  const zips = DATA.geojson.zips;
  const zipPrices = {};
  DATA.submarkets.forEach(s=>{
    s.zips.forEach(z=>{ if(s.redfin) zipPrices[z]=s.redfin; });
  });
  const priceVals = Object.values(zipPrices).map(v=>v.median_price).filter(x=>x);
  const lo=Math.min(...priceVals), hi=Math.max(...priceVals);
  const colorFor = p => {
    if(p==null) return '#555';
    const t=(p-lo)/(hi-lo);
    // teal (cheap) → gold (mid) → red (expensive)
    const stops=[[0,'#7dd3c0'],[0.5,'#e0a83a'],[1,'#e06767']];
    let a=stops[0], b=stops[stops.length-1];
    for(let i=0;i<stops.length-1;i++) if(t>=stops[i][0]&&t<=stops[i+1][0]){a=stops[i];b=stops[i+1];break;}
    const r=(t-a[0])/(b[0]-a[0]);
    const mix=(c1,c2)=>{const p1=parseInt(c1.slice(1),16),p2=parseInt(c2.slice(1),16);
      const rr=Math.round(((p1>>16)&255)*(1-r)+((p2>>16)&255)*r);
      const gg=Math.round(((p1>>8)&255)*(1-r)+((p2>>8)&255)*r);
      const bb=Math.round((p1&255)*(1-r)+(p2&255)*r);
      return `rgb(${rr},${gg},${bb})`;};
    return mix(a[1],b[1]);
  };

  LAYERS.price = L.geoJSON(zips,{
    style:f=>{
      const d = zipPrices[f.properties.zip];
      return {
        color:'#333', weight:1, fillOpacity:d?0.55:0.12,
        fillColor: d ? colorFor(d.median_price) : '#444'
      };
    },
    onEachFeature:(f,l)=>{
      const p=f.properties, d=zipPrices[p.zip];
      const tip = d
        ? `<b>${p.zip} · ${p.name}</b><br>Median ${fmt$(d.median_price)} · $${d.ppsf?.toFixed(0)}/sqft · YoY ${(d.yoy_price*100).toFixed(1)}%<br>DoM ${d.dom} · Inv ${d.inventory}`
        : `<b>${p.zip} · ${p.name}</b><br>No Redfin data`;
      l.bindTooltip(tip,{className:'z-tip',sticky:true});
    }
  });

  // Tax layer (SC 4% vs 6% highlight — really this shows the primary residence advantage)
  LAYERS.tax = L.geoJSON(zips,{
    style:f=>({color:'#333',weight:1,fillOpacity:0.35,fillColor:'#01696f'}),
    onEachFeature:(f,l)=>{
      const p=f.properties;
      l.bindTooltip(`<b>${p.zip} · ${p.name}</b><br>Assessment ratio: <b>4%</b> owner-occupied, 6% otherwise.<br>Est. ~280 mills combined (verify by TMS).<br>Owner-occupied is <b>exempt from school operating millage</b> (Act 388).`,{className:'z-tip',sticky:true});
    }
  });

  // School district polygons
  LAYERS.schools = L.geoJSON(DATA.geojson.districts,{
    style:f=>({color:'#b48be0',weight:2,fillOpacity:0.10,fillColor:'#b48be0'}),
    onEachFeature:(f,l)=>{
      l.bindTooltip("<b>"+f.properties.name+"</b><br>SC Dept of Education report card at <a href='https://ed.sc.gov/data/report-cards/' target='_blank'>ed.sc.gov</a>",{className:'z-tip',sticky:true});
    }
  });

  // Flood zones
  LAYERS.flood = L.geoJSON(DATA.geojson.flood,{
    style:{color:'#6ea8ff',weight:0.5,fillOpacity:0.55,fillColor:'#6ea8ff'},
    onEachFeature:(f,l)=>{ l.bindTooltip(f.properties.label||'FEMA SFHA',{className:'z-tip'}); }
  });

  // Drive-time isochrones as colored dots (grid of drive-time samples)
  const grid = DATA.drivetime.grid;
  const dtLayer = L.layerGroup();
  const bandColor = m => {
    if(m==null) return null;
    if(m<=15) return '#65c58b';
    if(m<=30) return '#e0a83a';
    if(m<=45) return '#e06767';
    return null;
  };
  grid.forEach(g=>{
    const c = bandColor(g.min);
    if(!c) return;
    L.circle([g.lat,g.lon],{radius:1400,color:c,weight:0,fillColor:c,fillOpacity:0.15}).addTo(dtLayer);
  });
  LAYERS.drive = dtLayer;

  // POI: grocery, hospital, ramps
  const pois = DATA.poi;
  const grp = {grocery:L.layerGroup(),hospital:L.layerGroup(),ramp:L.layerGroup()};
  const colors={grocery:'#65c58b',hospital:'#6ea8ff',ramp:'#b48be0'};
  const radii={grocery:3,hospital:5,ramp:2};
  pois.forEach(p=>{
    L.circleMarker([p.lat,p.lon],{radius:radii[p.c],fillColor:colors[p.c],color:'#111',weight:0.5,fillOpacity:0.9})
      .bindTooltip(`<b>${p.n}</b> · ${p.c}`,{className:'z-tip'})
      .addTo(grp[p.c]);
  });
  LAYERS.grocery = grp.grocery;
  LAYERS.hospital = grp.hospital;
  LAYERS.ramp = grp.ramp;

  // Submarket centroid pins with popups
  LAYERS.pins = L.layerGroup();
  DATA.submarkets.forEach(s=>{
    if(!s.redfin) return;
    // pick any centroid from first zip
    const zf = DATA.geojson.zips.features.find(f=>f.properties.zip===s.zips[0]);
    if(!zf) return;
    const [lat,lon] = zf.properties.centroid;
    const pin = L.circleMarker([lat,lon],{radius:8,fillColor:'#7dd3c0',color:'#111',weight:1.5,fillOpacity:0.95});
    const popup = `
      <b>${s.name}</b><br>
      Median: <b>${fmt$(s.redfin.median_price)}</b> · $${Math.round(s.redfin.ppsf)}/sqft · YoY ${(s.redfin.yoy_price*100).toFixed(1)}%<br>
      DoM ${Math.round(s.redfin.dom)} · Inv ${s.redfin.inventory} · Drive ${s.drive_min} min · District: ${s.district}<br>
      Fiber: ${s.fiber.providers.slice(0,2).join(', ')}<br>
      <span style="color:#9aa5ad">Source: <a href="https://www.redfin.com/news/data-center/" target="_blank">Redfin Data Center</a></span>
    `;
    pin.bindPopup(popup);
    pin.addTo(LAYERS.pins);
  });

  refreshMap();
}

const LAYER_META = [
  ['price','Median price $/sqft'],
  ['tax','Property tax (4/6% ratio)'],
  ['schools','School districts'],
  ['flood','FEMA flood zones'],
  ['drive','Drive time (15/30/45)'],
  ['grocery','Grocery'],
  ['hospital','Hospitals'],
  ['ramp','I-26 / I-85 ramps'],
  ['pins','Submarket pins'],
];
function renderLayerToggles(){
  const c=document.getElementById('layer-toggles');
  ACTIVE = new Set(['price','drive','pins']);
  c.innerHTML='';
  LAYER_META.forEach(([k,label])=>{
    const b=document.createElement('button');
    b.textContent=label; if(ACTIVE.has(k)) b.classList.add('on');
    b.onclick=()=>{
      if(ACTIVE.has(k)) ACTIVE.delete(k); else ACTIVE.add(k);
      b.classList.toggle('on'); refreshMap();
    };
    c.appendChild(b);
  });
}
function refreshMap(){
  LAYER_META.forEach(([k])=>{
    const layer = LAYERS[k];
    if(!layer) return;
    if(ACTIVE.has(k)){ if(!MAP.hasLayer(layer)) layer.addTo(MAP); }
    else{ if(MAP.hasLayer(layer)) MAP.removeLayer(layer); }
  });
}
function renderLegend(){
  const el = document.getElementById('legend');
  el.innerHTML = `
    <div><span class="sw" style="background:#7dd3c0"></span>Cheaper median</div>
    <div><span class="sw" style="background:#e0a83a"></span>Mid</div>
    <div><span class="sw" style="background:#e06767"></span>Pricier median</div>
    <div><span class="sw" style="background:#65c58b"></span>≤15 min drive</div>
    <div><span class="sw" style="background:#e0a83a"></span>15–30 min</div>
    <div><span class="sw" style="background:#e06767"></span>30–45 min</div>
    <div><span class="sw" style="background:#6ea8ff"></span>FEMA SFHA (A/AE)</div>
    <div><span class="sw" style="background:#b48be0"></span>School district / ramp</div>
  `;
}

/* ---------------- INPUTS ---------------- */
function bindGlobals(){
  ['inc','exp','dti','down','price','rate','hoa','w-fiber'].forEach(k=>{
    const el=document.getElementById('i-'+k);
    el.addEventListener('input',()=>updateAll());
  });
}
function readNum(id){ return parseFloat(document.getElementById(id).value); }
function updateAll(){
  const inc=readNum('i-inc'), exp=readNum('i-exp'), dti=readNum('i-dti');
  const down=readNum('i-down'), price=readNum('i-price'), rate=readNum('i-rate')/100, hoa=readNum('i-hoa');
  document.getElementById('l-inc').textContent = fmt$(inc);
  document.getElementById('l-exp').textContent = fmt$(exp);
  document.getElementById('l-dti').textContent = dti+'%';
  document.getElementById('l-down').textContent = fmt$(down);
  document.getElementById('l-price').textContent = fmt$(price);
  document.getElementById('l-rate').textContent = readNum('i-rate').toFixed(2)+'%';
  document.getElementById('l-hoa').textContent = fmt$(hoa);
  document.getElementById('l-w-fiber').textContent = readNum('i-w-fiber')+'%';

  const loan = Math.max(0, price - down);
  const n=360, r=rate/12;
  const pi = r>0 ? loan*(r*Math.pow(1+r,n))/(Math.pow(1+r,n)-1) : loan/n;
  // Tax: SC owner-occupied 4% ratio × ~280 mills (est.), school-operating exempt (Act 388)
  const mills = DATA.global.tax.typical_owner_millage_mills;
  const taxMo = price * DATA.global.tax.primary_assessment_ratio * (mills/1000) / 12;
  const insMo = DATA.global.insurance.sc_avg_annual / 12;
  const piti = pi + taxMo + insMo + hoa;
  const fdti = piti / (inc/12);
  const closing = price * 0.03;
  document.getElementById('k-loan').textContent = fmt$(loan);
  document.getElementById('k-loan-d').textContent = `Price ${fmt$(price)} − Down ${fmt$(down)}`;
  document.getElementById('k-pi').textContent = fmt$(pi);
  document.getElementById('k-tax').textContent = fmt$(taxMo);
  document.getElementById('k-ins').textContent = fmt$(insMo);
  document.getElementById('k-hoa').textContent = fmt$(hoa);
  document.getElementById('k-piti').textContent = fmt$(piti);
  document.getElementById('k-fdti').textContent = (fdti*100).toFixed(1)+'%';
  const fEl=document.getElementById('k-fdti-d');
  if(fdti*100 <= dti){ fEl.textContent = `Under ${dti}% target ✓`; fEl.className='d up'; }
  else{ fEl.textContent = `Over ${dti}% target — cut price or grow down`; fEl.className='d down'; }
  document.getElementById('k-ctc').textContent = fmt$(down + closing);

  // Verdict
  const rentEq = 2200; // Spartanburg 3-bed rent baseline
  const delta = piti - rentEq;
  const verdict = document.getElementById('afford-verdict');
  const decision = (fdti*100 <= dti && piti > 0) ? 'take' : 'watch';
  verdict.innerHTML = `<b>Verdict:</b> <span class="chip ${decision}">${decision==='take'?'TAKE':'WATCH'}</span> at ${fmt$(price)} · rate ${(rate*100).toFixed(2)}%. PITI ${fmt$(piti)} vs. ~${fmt$(rentEq)} rent (${delta>=0?'+':''}${fmt$(delta)}/mo). Cash to close ${fmt$(down+closing)}. Sensitivity: a +1% rate move adds ~${fmt$(loan*0.01/12*7)}/mo on this loan.`;

  updateBE(); updateProperty(); updateRunway(); renderScorecard();
}

/* ---------------- RENT VS BUY ---------------- */
function bindRentBuy(){
  ['rent','appr','invret','sell'].forEach(k=>{
    document.getElementById('i-'+k).addEventListener('input',updateBE);
  });
  ['rent','appr','invret','sell'].forEach(k=>{});
}
function updateBE(){
  const rent=readNum('i-rent'), appr=readNum('i-appr')/100, ir=readNum('i-invret')/100, sell=readNum('i-sell')/100;
  document.getElementById('l-rent').textContent=fmt$(rent);
  document.getElementById('l-appr').textContent=readNum('i-appr').toFixed(1)+'%';
  document.getElementById('l-invret').textContent=readNum('i-invret').toFixed(1)+'%';
  document.getElementById('l-sell').textContent=readNum('i-sell').toFixed(1).replace('.0','')+'%';

  const price=readNum('i-price'), down=readNum('i-down'), rate=readNum('i-rate')/100, hoa=readNum('i-hoa');
  const closing = price*0.03;
  const loan = Math.max(0, price-down);
  const n=360, r=rate/12;
  const pi = r>0 ? loan*(r*Math.pow(1+r,n))/(Math.pow(1+r,n)-1) : loan/n;
  const mills = DATA.global.tax.typical_owner_millage_mills;
  const taxMo0 = price*DATA.global.tax.primary_assessment_ratio*(mills/1000)/12;
  const insMo0 = DATA.global.insurance.sc_avg_annual/12;

  const years=[], buyNet=[], rentNet=[];
  let equity=down, homeVal=price, balance=loan;
  let investBalance=down+closing;
  const rentAnnGrowth=0.03, taxIns=(taxMo0+insMo0)*12, insGrow=0.06;
  let currRent=rent;
  for(let y=1;y<=15;y++){
    for(let m=1;m<=12;m++){
      const interest=balance*r;
      const principal=pi-interest;
      balance=Math.max(0,balance-principal);
    }
    homeVal*=(1+appr);
    const sellCosts=homeVal*sell;
    const buyPos = (homeVal - balance) - sellCosts - closing;
    investBalance*=(1+ir); // opportunity cost
    // rentPos: pay rent + save the mortgage-vs-rent delta? Assume equivalent lifestyle: rent path adds cumulative rent payments, but invests the down/closing
    const monthlyOwn = pi + (taxIns/12)*(Math.pow(1+insGrow,y-1)) + hoa;
    // For simplicity: rent path invests (down+closing) at ir; buy path locks in monthly own vs monthly rent
    // Break-even when buyPos + (cumulative rent-own savings compounded) - (rent path invest balance) >= 0
    const rentPos = investBalance - 0; // baseline
    years.push(y);
    buyNet.push(buyPos);
    rentNet.push(rentPos);
    currRent*=(1+rentAnnGrowth);
  }
  // Break-even year: first year buyNet > rentNet
  let be=null;
  for(let i=0;i<years.length;i++){ if(buyNet[i]>=rentNet[i]){ be=years[i]; break; } }
  document.getElementById('k-be').textContent = be? be+' years' : '>15 years';
  document.getElementById('k-be-d').textContent = be? (be<=5?'Fast payoff — buy leans strong':(be<=7?'Reasonable if you stay 7+':'Long payoff — rent longer or negotiate price')): 'Longer than 15 years — do not buy at these inputs';

  const ctx=document.getElementById('be-chart');
  const text = getCss('--text-muted'), grid=getCss('--divider');
  if(BE_CHART) BE_CHART.destroy();
  BE_CHART = new Chart(ctx,{
    type:'line',
    data:{labels:years,datasets:[
      {label:'Buy net position',data:buyNet,borderColor:getCss('--primary'),backgroundColor:'rgba(125,211,192,.10)',tension:.25,fill:true,pointRadius:0,borderWidth:2},
      {label:'Rent path (invest down at return)',data:rentNet,borderColor:getCss('--gold'),backgroundColor:'rgba(224,168,58,.08)',tension:.25,fill:true,pointRadius:0,borderWidth:2}
    ]},
    options:{
      animation:false, responsive:true, maintainAspectRatio:false,
      scales:{
        x:{grid:{color:grid},ticks:{color:text,font:{size:11}},title:{display:true,text:'Years owned',color:text}},
        y:{grid:{color:grid},ticks:{color:text,font:{size:11},callback:v=>'$'+(v/1000).toFixed(0)+'K'}}
      },
      plugins:{legend:{labels:{color:text,font:{size:11}}}, tooltip:{callbacks:{label:c=>c.dataset.label+': '+fmt$(c.raw)}}}
    }
  });
}
function getCss(v){return getComputedStyle(document.documentElement).getPropertyValue(v).trim();}

/* ---------------- SCORECARD ---------------- */
let SORT_KEY='score', SORT_DIR=-1;
function renderScorecard(){
  const body = document.getElementById('score-body');
  const wFiber = readNum('i-w-fiber')/100;
  const wRest = (1-wFiber)/4;
  const weights = {price:wRest, leverage:wRest, commute:wRest, safety:wRest, fiber:wFiber};
  document.getElementById('weight-tags').innerHTML = Object.entries(weights).map(([k,v])=>`<span class="tag${k==='fiber'?' h':''}">${k}: ${(v*100).toFixed(0)}%</span>`).join('');

  const rows = DATA.submarkets.map(s=>{
    const sc = s.scores;
    const composite = weights.price*sc.price + weights.leverage*sc.leverage + weights.commute*sc.commute + weights.safety*sc.safety + weights.fiber*sc.fiber;
    // penalize deal-breakers: >20 min commute is bad
    const commutePenalty = s.drive_min > 20 ? 0.85 : 1;
    const finalScore = composite*commutePenalty;
    return {s, finalScore};
  });
  const map = {
    name:r=>r.s.name, price:r=>r.s.redfin?.median_price??0, ppsf:r=>r.s.redfin?.ppsf??0,
    yoy:r=>r.s.redfin?.yoy_price??0, dom:r=>r.s.redfin?.dom??0, inv:r=>r.s.redfin?.inventory??0,
    drive:r=>r.s.drive_min, score:r=>r.finalScore
  };
  rows.sort((a,b)=>{
    const av=map[SORT_KEY](a), bv=map[SORT_KEY](b);
    if(typeof av==='string') return SORT_DIR*av.localeCompare(bv);
    return SORT_DIR*(av-bv);
  });
  // Rank top-2 and worst by score
  const ranked=[...rows].sort((a,b)=>b.finalScore-a.finalScore);
  const top2 = new Set(ranked.slice(0,2).map(r=>r.s.id));
  const bottom = new Set(ranked.slice(-2).map(r=>r.s.id));

  body.innerHTML = rows.map(({s,finalScore})=>{
    const r=s.redfin;
    const decision = top2.has(s.id) ? '<span class="chip take">TAKE (top 2)</span>' : bottom.has(s.id) ? '<span class="chip pass">PASS</span>' : '<span class="chip watch">WATCH</span>';
    const cls = top2.has(s.id) ? 'top-2' : bottom.has(s.id) ? 'reject' : '';
    return `<tr class="${cls}">
      <td><b>${s.name}</b><br><span style="color:var(--text-faint);font-size:var(--text-xs)">${s.zips.join(', ')} · ${s.district}</span></td>
      <td class="num">${fmt$k(r?.median_price)}</td>
      <td class="num">$${Math.round(r?.ppsf||0)}</td>
      <td class="num ${(r?.yoy_price>0)?'up':(r?.yoy_price<0?'down':'flat')}">${((r?.yoy_price||0)*100).toFixed(1)}%</td>
      <td class="num">${Math.round(r?.dom||0)}</td>
      <td class="num">${r?.inventory||0}</td>
      <td class="num">${s.drive_min}m</td>
      <td class="num"><span class="bar" style="width:${Math.max(4,finalScore*70)}px"></span>${(finalScore*100).toFixed(0)}</td>
      <td>${decision}</td>
    </tr>`;
  }).join('');

  // Bind sort clicks once
  if(!renderScorecard.bound){
    renderScorecard.bound=true;
    document.querySelectorAll('#score-table th').forEach(th=>{
      th.addEventListener('click',()=>{
        const k=th.dataset.sort; if(!k) return;
        if(SORT_KEY===k) SORT_DIR*=-1; else{SORT_KEY=k;SORT_DIR=(k==='name')?1:-1;}
        renderScorecard();
      });
    });
  }
}

/* ---------------- TIMING ---------------- */
function renderTiming(){
  const inds = [
    ['Months supply · SAR','4.3 mo (Jul 2026)','>5.0 mo = buyer market','<3.5 mo = leverage gone','https://scr.stats.showingtime.com/docs/mmi/x/MarketActivityfortheSpartanburgAssociationofREALTORS?src=map'],
    ['Active inventory','2,477 (+52.6% vs 2024)','>2,700 rising','<2,200 sustained','https://scr.stats.showingtime.com/docs/mmi/x/MarketActivityfortheSpartanburgAssociationofREALTORS?src=map'],
    ['Median sale price YoY','−2.8% (Jul 2026)','Two more −YoY quarters','Return to +4% or more','https://scr.stats.showingtime.com/docs/mmi/x/MarketActivityfortheSpartanburgAssociationofREALTORS?src=map'],
    ['Days on market','55 (+31%)','>70 days','<45 days','https://scr.stats.showingtime.com/docs/mmi/x/MarketActivityfortheSpartanburgAssociationofREALTORS?src=map'],
    ['New listings YoY','−14.5%','Turns positive','Deepens past −20%','https://scr.stats.showingtime.com/docs/mmi/x/MarketActivityfortheSpartanburgAssociationofREALTORS?src=map'],
    ['30-yr PMMS rate','6.67% (Aug 13, 2026)','Sustained <6.25%','Sustained >7.25%','https://www.freddiemac.com/pmms'],
    ['BMW Plant Spartanburg','>11,000 emp · Woodruff opens Dec 2026','Hiring at Woodruff','Any shift cut or WARN','https://www.press.bmwgroup.com/usa/article/detail/T0458940EN_US/'],
    ['SC WARN — Spartanburg','2026: 130 + 195 confirmed','No new notices','Any single >500 workers','https://www.dew.sc.gov/sites/dew/files/Documents/WARN%2520Report%252005%252022%25202026.pdf'],
    ['County residential permits','3,986 (2025 record)','2026 <3,200','2026 >4,000','https://fred.stlouisfed.org/data/BPPRIV045083.txt']
  ];
  document.getElementById('timing-body').innerHTML = inds.map(([n,cur,g,b,src])=>`
    <div style="border-bottom:1px solid var(--divider);padding:8px 0">
      <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px">
        <b>${n}</b>
        <span style="color:var(--text-muted);font-size:var(--text-xs)">${cur}</span>
      </div>
      <div style="font-size:var(--text-xs);color:var(--text-muted);margin-top:3px">
        <span style="color:var(--green)">Better: ${g}</span>
        &nbsp;·&nbsp;
        <span style="color:var(--red)">Worse: ${b}</span>
        &nbsp;·&nbsp;
        <a href="${src}" target="_blank" style="color:var(--text-faint)">source</a>
      </div>
    </div>`).join('');
}

/* ---------------- PROPERTY SCORER ---------------- */
function bindProperty(){
  ['addr','price','bb','sqft','hoa','gar','fib','com','sep','fld','roof','hvac'].forEach(k=>{
    document.getElementById('p-'+k).addEventListener('input',updateProperty);
  });
}
function updateProperty(){
  const price=+document.getElementById('p-price').value||0;
  const sqft=+document.getElementById('p-sqft').value||0;
  const hoa=+document.getElementById('p-hoa').value||0;
  const gar=+document.getElementById('p-gar').value||0;
  const fib=+document.getElementById('p-fib').value||0;
  const com=+document.getElementById('p-com').value||0;
  const sep=+document.getElementById('p-sep').value||0;
  const fld=+document.getElementById('p-fld').value||0;
  const roof=+document.getElementById('p-roof').value||0;
  const hvac=+document.getElementById('p-hvac').value||0;
  const [beds,baths] = (document.getElementById('p-bb').value||'0/0').split('/').map(s=>+s.trim());
  const targetPrice = readNum('i-price');

  let score=100, flags=[];
  // Deal-breakers: instant fail
  if(hoa>100){score=0; flags.push(`HOA $${hoa}/mo exceeds your $100/mo deal-breaker → PASS`);}
  if(fld===1){score=0; flags.push('In FEMA flood zone → PASS (deal-breaker)');}
  if(sep===1){score=0; flags.push('Well/septic → PASS (deal-breaker)');}
  if(com>20){score=0; flags.push(`${com}-min commute exceeds 20-min rule → PASS`);}

  // Must-haves partial credit
  if(beds<3){score-=20; flags.push(`Only ${beds} bedrooms (want 3)`);}
  if(baths<3){score-=8; flags.push(`Only ${baths} baths (want 3)`);}
  if(sqft<1400){score-=20; flags.push(`${sqft} sqft below 1,400 floor`);}
  if(gar<2){score-=10; flags.push(`Only ${gar}-car garage (want 2)`);}
  if(fib===0){score-=15; flags.push('No fiber — check FCC broadband map before offer');}

  // Age of systems
  if(roof>=15) flags.push(`Roof age ${roof} yrs — expect replacement soon; escrow or credit`);
  if(hvac>=12) flags.push(`HVAC age ${hvac} yrs — inspect + budget replacement`);

  // Price vs target
  if(price > targetPrice*1.1) flags.push(`Price ${fmt$(price)} is ${((price/targetPrice-1)*100).toFixed(0)}% above your target — negotiate`);
  if(price/sqft > 200) flags.push(`Above $200/sqft — check comps in the same ZIP`);

  // Recommendations
  flags.push('Verify SC 4% owner-occupied residence classification is filed by Jan 15');
  flags.push('Pull tax history vs. estimated post-purchase bill (assessment resets to sale price)');
  flags.push('Get an actual insurance quote before finalizing the offer');
  flags.push('Pull permit history at Spartanburg County GIS');

  score = Math.max(0, Math.min(100,score));
  document.getElementById('p-score').textContent = score;
  document.getElementById('p-score').style.color = score>=75?'var(--green)':score>=50?'var(--gold)':'var(--red)';
  document.getElementById('p-score-d').textContent = score>=75?'Strong fit · take to inspection':score>=50?'Watch · negotiate or wait':'Pass · does not fit your rules';
  document.getElementById('p-flags').innerHTML = flags.map(f=>`<li>${f}</li>`).join('');
}

/* ---------------- RUNWAY ---------------- */
function bindRunway(){
  ['save','msave','dpct'].forEach(k=>document.getElementById('i-'+k).addEventListener('input',updateRunway));
}
function updateRunway(){
  const save=readNum('i-save'), msave=readNum('i-msave'), dpct=readNum('i-dpct')/100;
  const price=readNum('i-price');
  document.getElementById('l-save').textContent=fmt$(save);
  document.getElementById('l-msave').textContent=fmt$(msave);
  document.getElementById('l-dpct').textContent=(dpct*100).toFixed(0)+'% of price = '+fmt$(price*dpct);

  const monthsToLease = 12; // Aug 2026 → Aug 2027 ≈ 12 months
  const labels=[], balance=[]; let b=save;
  for(let m=0;m<=monthsToLease;m++){
    labels.push('M+'+m);
    balance.push(b);
    b += msave;
  }
  const target = price*dpct;
  const projected = save + msave*monthsToLease;
  const status = projected>=target ? 'On track' : 'Short by '+fmt$(target-projected);
  document.getElementById('k-runway').textContent=status;
  document.getElementById('k-runway').style.color = projected>=target?'var(--green)':'var(--red)';
  document.getElementById('k-runway-d').textContent = `Projected by lease-end: ${fmt$(projected)} vs target ${fmt$(target)}`;

  const ctx=document.getElementById('runway-chart');
  const text = getCss('--text-muted'), grid=getCss('--divider');
  if(RUNWAY_CHART) RUNWAY_CHART.destroy();
  RUNWAY_CHART = new Chart(ctx,{
    type:'line',
    data:{labels,datasets:[
      {label:'Savings balance',data:balance,borderColor:getCss('--primary'),backgroundColor:'rgba(125,211,192,.10)',fill:true,tension:.2,pointRadius:0,borderWidth:2},
      {label:'Target down-payment',data:labels.map(()=>target),borderColor:getCss('--gold'),borderDash:[6,4],pointRadius:0,borderWidth:1.5,fill:false}
    ]},
    options:{
      animation:false, responsive:true, maintainAspectRatio:false,
      scales:{
        x:{grid:{color:grid},ticks:{color:text,font:{size:11},maxTicksLimit:6}},
        y:{grid:{color:grid},ticks:{color:text,font:{size:11},callback:v=>'$'+(v/1000).toFixed(0)+'K'}}
      },
      plugins:{legend:{labels:{color:text,font:{size:11}}}}
    }
  });
}

boot();
