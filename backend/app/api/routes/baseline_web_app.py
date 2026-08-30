"""Telegram Web App shell for the adaptive baseline form."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import parse_qsl

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.schemas.common import TelegramIdentity
from app.services.onboarding.service import OnboardingService

router = APIRouter(tags=["telegram-web-app"])

_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{color-scheme:light dark}*{box-sizing:border-box}body{font:16px system-ui,-apple-system,sans-serif;margin:0;padding:16px;background:var(--tg-theme-secondary-bg-color,#f4f4f5);color:var(--tg-theme-text-color,#111)}main{max-width:560px;margin:auto}h1{font-size:24px;margin:4px 0 8px}h2{font-size:18px;margin:0}p{line-height:1.45}.intro{margin:0 0 20px;color:var(--tg-theme-hint-color,#666)}.section{background:var(--tg-theme-bg-color,#fff);border-radius:14px;padding:16px;margin:14px 0}.field{margin-top:18px}.field:first-of-type{margin-top:12px}label{display:block;font-weight:650;margin-bottom:6px}.hint{display:block;color:var(--tg-theme-hint-color,#666);font-size:13px;margin:0 0 8px}input,select{width:100%;padding:12px;border:1px solid var(--tg-theme-hint-color,#a1a1aa);border-radius:9px;background:var(--tg-theme-bg-color,#fff);color:var(--tg-theme-text-color,#111);font:inherit}.optional{font-weight:400}.error{background:#fee2e2;color:#991b1b;border-radius:10px;padding:12px;margin:0 0 14px}.hidden{display:none}button{width:100%;margin:10px 0 24px;padding:14px;border:0;border-radius:10px;background:var(--tg-theme-button-color,#2481cc);color:var(--tg-theme-button-text-color,#fff);font:inherit;font-weight:700}</style>
</head><body><main><h1>Your training baseline</h1><p class="intro">Use your usual training from the last four weeks. A few approximate answers are enough to create a conservative first week.</p><p id="error" class="error hidden"></p><form id="form"></form></main>
<script>
const config={
 'running.typical_weekly_sessions':{section:'Running',label:'Typical runs per week',kind:'number',min:0,max:14,help:'Use your average across the last 4 weeks.'},
 'running.typical_weekly_duration_minutes':{section:'Running',label:'Typical total running minutes per week',kind:'number',min:0,max:1440,help:'Use 0 if you have not run.'},
 'running.longest_recent_run_minutes':{section:'Running',label:'Longest run in the last 14 days (minutes)',kind:'number',min:0,max:1440,help:'Use 0 if you have not run.'},
 'running.recent_race_result':{section:'Running',label:'Recent race or time trial',kind:'text',optional:true,placeholder:'For example: 5 km, 25:30',help:'Optional. Leave blank if you do not have one.'},
 'cycling.typical_weekly_sessions':{section:'Cycling',label:'Typical rides per week',kind:'number',min:0,max:14,help:'Use your average across the last 4 weeks.'},
 'cycling.typical_weekly_duration_minutes':{section:'Cycling',label:'Typical total cycling minutes per week',kind:'number',min:0,max:1440,help:'Use 0 if you have not ridden.'},
 'cycling.longest_recent_ride_minutes':{section:'Cycling',label:'Longest ride in the last 14 days (minutes)',kind:'number',min:0,max:1440,help:'Use 0 if you have not ridden.'},
 'cycling.riding_environment':{section:'Cycling',label:'Where can you currently ride?',kind:'select',options:[['INDOOR','Indoor trainer'],['OUTDOOR','Outdoors'],['BOTH','Both'],['NONE','I cannot currently ride']]},
 'cycling.riding_confidence':{section:'Cycling',label:'Cycling confidence',kind:'select',options:[['NEW_RIDER','New rider'],['SIMPLE_ROUTES','Comfortable on simple routes'],['CONFIDENT','Confident with traffic, hills, and descents'],['NOT_CURRENTLY_RIDING','Not currently riding']]},
 'cycling.recent_ftp_watts':{section:'Cycling',label:'Recent FTP (watts)',kind:'number',min:1,max:1000,optional:true,help:'Optional. Only enter a recent, measured value.'},
 'swimming.typical_weekly_sessions':{section:'Swimming',label:'Typical swims per week',kind:'number',min:0,max:14,help:'Use your average across the last 4 weeks.'},
 'swimming.typical_weekly_duration_minutes':{section:'Swimming',label:'Typical total swimming minutes per week',kind:'number',min:0,max:1440,help:'Use 0 if you have not swum.'},
 'swimming.longest_continuous_swim_meters':{section:'Swimming',label:'Longest continuous swim (meters)',kind:'number',min:0,max:100000,help:'Use 0 if you have not swum.'},
 'swimming.swimming_environment':{section:'Swimming',label:'Where can you currently swim?',kind:'select',options:[['POOL','Pool'],['OPEN_WATER','Open water'],['BOTH','Both'],['NONE','I cannot currently swim']]},
 'swimming.pool_length_meters':{section:'Swimming',label:'Pool length',kind:'select',optional:true,options:[['25','25 meters'],['50','50 meters']],help:'Optional. Leave blank if you do not use a pool.'},
 'swimming.recent_400m_seconds':{section:'Swimming',label:'Recent 400m swim time',kind:'text',optional:true,placeholder:'For example: 8:30',help:'Optional. Leave blank if you do not have one.'},
 'triathlon.prior_experience':{section:'Triathlon',label:'Prior triathlon experience',kind:'select',options:[['NONE','None yet'],['SPRINT','Sprint'],['OLYMPIC','Olympic distance'],['LONG_COURSE','70.3 or full distance']]},
 'triathlon.weakest_discipline':{section:'Triathlon',label:'Your weakest discipline right now',kind:'select',options:[['RUNNING','Running'],['CYCLING','Cycling'],['SWIMMING','Swimming'],['NO_CLEAR_WEAKNESS','No clear weakness']]},
 'triathlon.open_water_confidence':{section:'Triathlon',label:'Open-water swimming confidence',kind:'select',options:[['NOT_CONFIDENT','Not confident yet'],['SOME_EXPERIENCE','Some experience'],['CONFIDENT','Confident']]}
};
const form=document.querySelector('#form');
const requestedKeys=(new URLSearchParams(location.search).get('fields')||'').split(',').filter(Boolean);
const requested=requestedKeys.filter(key=>config[key]);
const hasUnsupportedFields=requested.length!==requestedKeys.length;
const errorKey=new URLSearchParams(location.search).get('error');
if(errorKey&&config[errorKey]){const error=document.querySelector('#error');error.textContent=`Please check “${config[errorKey].label}” and submit again.`;error.classList.remove('hidden')}
let currentSection='';let section;
for(const key of requested){const item=config[key];if(item.section!==currentSection){currentSection=item.section;section=document.createElement('section');section.className='section';const title=document.createElement('h2');title.textContent=currentSection;section.append(title);form.append(section)}const field=document.createElement('div');field.className='field';const label=document.createElement('label');label.htmlFor=key;label.textContent=item.label+(item.optional?' (optional)':'');field.append(label);if(item.help){const help=document.createElement('span');help.className='hint';help.textContent=item.help;field.append(help)}let input;if(item.kind==='select'){input=document.createElement('select');input.innerHTML='<option value="">Choose</option>';for(const [value,text] of item.options){const option=document.createElement('option');option.value=value;option.textContent=text;input.append(option)}}else{input=document.createElement('input');input.type=item.kind;input.inputMode=item.kind==='number'?'numeric':'text';input.placeholder=item.placeholder||'';if(item.min!==undefined)input.min=item.min;if(item.max!==undefined)input.max=item.max;if(item.kind==='number')input.step='1'}input.id=key;input.name=key;input.required=!item.optional;field.append(input);section.append(field)}
if(!requested.length||hasUnsupportedFields){form.innerHTML='<section class="section"><p>This baseline form is out of date. Return to the coach and open the newest form again.</p></section>'}else{const button=document.createElement('button');button.type='submit';button.textContent='Save baseline';form.append(button)}
const webApp=window.Telegram&&window.Telegram.WebApp;webApp&&webApp.ready();
form.addEventListener('submit',async event=>{event.preventDefault();if(!form.reportValidity())return;const data=Object.fromEntries(new FormData(form));if(!webApp||!webApp.initData){alert('Open this form from Telegram to save it.');return}const button=form.querySelector('button');button.disabled=true;button.textContent='Saving…';try{const response=await fetch('/webapp/baseline/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:webApp.initData,values:data})});if(!response.ok)throw new Error();webApp.close()}catch(_){button.disabled=false;button.textContent='Save baseline';alert('Unable to save your baseline. Please try again.')}});
</script></body></html>"""


@router.get("/webapp/baseline", response_class=HTMLResponse)
async def baseline_web_app() -> str:
    return _PAGE


@router.post("/webapp/baseline/submit")
async def submit_baseline(request: Request) -> dict[str, bool]:
    body = await request.json()
    init_data = body.get("init_data")
    values = body.get("values")
    if not isinstance(init_data, str) or not isinstance(values, dict):
        raise HTTPException(status_code=400, detail="invalid payload")
    settings = request.app.state.settings
    token = settings.telegram_bot_token
    if token is None:
        raise HTTPException(status_code=503, detail="bot unavailable")
    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = data.pop("hash", "")
    check_string = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret = hmac.new(b"WebAppData", token.get_secret_value().encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        raise HTTPException(status_code=401, detail="invalid Telegram session")
    try:
        user = json.loads(data["user"])
        identity = TelegramIdentity(
            telegram_user_id=user["id"], telegram_username=user.get("username"),
            first_name=user.get("first_name"), language_code=user.get("language_code", "en"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail="invalid Telegram user") from error
    service = OnboardingService(session_factory=request.app.state.session_factory, settings=settings)
    await service.submit_baseline_form(identity, values)
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"https://api.telegram.org/bot{token.get_secret_value()}/sendMessage",
            json={
                "chat_id": identity.telegram_user_id,
                "text": "Baseline saved. I have your starting point and will use completed workouts to refine your plan.",
            },
        )
    return {"ok": True}
