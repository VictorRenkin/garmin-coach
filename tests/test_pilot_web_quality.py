"""Pilot checks for interrupted web requests; no external services."""
import json
import re
import shutil
import subprocess

import pytest
from garmin_coach import dashboard


@pytest.mark.parametrize('failure',['transport','non_json'])
def test_sync_failure_reports_interruption_and_restores_action_without_reloading_history(failure):
    html=dashboard.TEMPLATE_FILE.read_text(encoding='utf-8')
    handler=re.search(r"  \$\('refreshBtn'\).onclick=async\(\)=>\{.*?\};bindChartInteractions",html).group(0).removesuffix(';bindChartInteractions')
    script='''
const elements={refreshBtn:{disabled:false,textContent:'Mettre à jour mes activités'},syncStatus:{textContent:''}};
const $=id=>elements[id];
let reloads=0;const loadAll=async()=>{reloads++};
const existingHistory=Object.freeze([{id:'synthetic-original'}]);const state={activities:existingHistory};
let requests=0;let fetch=async()=>{requests++;if(FAILURE==='transport')throw new TypeError('Synthetic socket lost');return {ok:false,json:async()=>{throw new SyntaxError('Synthetic bad JSON')}}};
'''.replace('FAILURE',json.dumps(failure))+handler+''';
(async()=>{let rejected=false;try{await elements.refreshBtn.onclick()}catch{rejected=true}
 const failed={rejected,reloads,requests,historyPreserved:state.activities===existingHistory,refreshBtn:{...elements.refreshBtn},syncStatus:{...elements.syncStatus}};
 fetch=async()=>{requests++;return {ok:true,json:async()=>({message:'Synthetic completed'})}};
 await elements.refreshBtn.onclick();
 console.log(JSON.stringify({...failed,manualRetry:{requests,reloads,disabled:elements.refreshBtn.disabled,status:elements.syncStatus.textContent}}));})();
'''
    result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,encoding='utf-8',check=True)
    data=json.loads(result.stdout)
    assert data['rejected'] is False
    assert data['requests']==1
    assert data['manualRetry']=={'requests':2,'reloads':1,'disabled':False,'status':'Synthetic completed'}
    assert data['refreshBtn']=={'disabled':False,'textContent':'Mettre à jour mes activités'}
    assert data['historyPreserved'] is True and data['reloads']==0
    assert 'interrompue' in data['syncStatus']['textContent']
    assert 'Synthetic' not in data['syncStatus']['textContent']
