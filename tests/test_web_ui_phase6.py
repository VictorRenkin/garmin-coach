"""Execute real web renderers for reproduced consent/uncertain-delivery states."""
import json
import re
import shutil
import subprocess

from garmin_coach import dashboard


def render(function, *, acceptance='accepted', delivery=None):
    html = dashboard.TEMPLATE_FILE.read_text(encoding='utf-8')
    names = ('deliveryFor', 'deliveryStep', 'garminDeliveryMarkup', 'sessionAcceptance', 'sessionDecisionMarkup')
    functions = '\n'.join(re.search(r'  function '+name+r'\(.*', html).group(0) for name in names)
    script = 'const state='+json.dumps({'planAcceptance':{'status':'partially_accepted','sessions':[{'session_id':'morning','status':acceptance}]},'garminDelivery':{'sessions':[delivery] if delivery else []}})+';\n'
    script += 'const esc=v=>String(v??"").replaceAll("<","&lt;").replaceAll(">","&gt;");\n'+functions
    script += '\nconsole.log(JSON.stringify('+function+'({session_id:"morning"},0)));'
    return json.loads(subprocess.run([shutil.which('node'),'-e',script],capture_output=True,encoding='utf-8',check=True).stdout)


def test_refused_session_displays_saved_refusal_and_can_be_explicitly_reconsidered():
    markup=render('sessionDecisionMarkup',acceptance='refused')
    assert 'Séance refusée' in markup
    assert 'Aucun envoi Garmin' in markup
    assert 'data-session-decision="accepted"' in markup
    assert 'Séance validée individuellement' not in markup


def test_unknown_upload_never_claims_not_created_or_offers_another_upload():
    markup=render('garminDeliveryMarkup',delivery={'session_index':0,'supported':True,
        'garmin_connect_status':'unknown','upload_status':'uncertain','calendar_status':'not_scheduled',
        'last_error':'Création Garmin incertaine ; aucun nouvel envoi avant vérification.'})
    assert 'Création incertaine' in markup
    assert 'Pas encore créé' not in markup
    assert 'data-garmin-send' not in markup
    assert 'Envoyer vers Garmin' not in markup
    assert 'seront exportés lors de la création' not in markup


def test_confirmed_id_retains_calendar_verification_without_reupload_label():
    markup=render('garminDeliveryMarkup',delivery={'session_index':0,'supported':True,
        'garmin_connect_status':'created','upload_status':'confirmed','garmin_workout_id':701,
        'calendar_status':'verification_pending'})
    assert 'Créé · ID 701' in markup
    assert 'Vérifier le calendrier Garmin' in markup
    assert 'data-garmin-send="0"' in markup
    assert 'Envoyer vers Garmin' not in markup


def test_unaccepted_session_never_exposes_delivery_action():
    markup=render('garminDeliveryMarkup',acceptance='refused',delivery={'session_index':0,'supported':True})
    assert 'data-garmin-send' not in markup
    assert 'Aucun envoi Garmin' in markup

def test_session_actions_are_bound_after_journey_cards_and_use_local_feedback():
    html=dashboard.TEMPLATE_FILE.read_text(encoding='utf-8')
    assert 'renderFirstRunJourney();bindPlanSessionActions();' in html
    helper=re.search(r'  function bindPlanSessionActions\(.*',html).group(0)
    script='''
const calls=[],feedback={scope:'visible-card'};
const decision={dataset:{sessionId:'evening',sessionDecision:'refused'},closest:()=>({querySelector:()=>feedback})};
const send={dataset:{garminSend:'1'}};
const document={querySelectorAll:selector=>selector==='[data-garmin-send]'?[send]:[decision]};
const decidePlanProposalV2=(kind,options)=>calls.push([kind,options.sessionId,options.helpElement.scope]);
const sendSessionToGarmin=(index,button)=>calls.push([index,button===send]);
'''+helper+'''
bindPlanSessionActions();decision.onclick();send.onclick();console.log(JSON.stringify(calls));
'''
    result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,encoding='utf-8',check=True)
    assert json.loads(result.stdout)==[['refused','evening','visible-card'],[1,True]]

def test_failed_send_refreshes_persisted_delivery_state_before_reporting_error():
    html=dashboard.TEMPLATE_FILE.read_text(encoding='utf-8')
    function=re.search(r'  async function sendSessionToGarmin\(.*',html).group(0)
    script='''
let refreshes=0;
const state={planProposal:{proposal:{sessions:[{session_id:'morning',date:'2099-10-13'}]}}};
const garminDeliveryPending=new Set(),deliveryFor=()=>null,confirm=()=>true,fmtDate=v=>v,esc=v=>v;
const fetch=async()=>({ok:false,json:async()=>({message:'Un précédent envoi reste incertain.'})});
const loadAll=async()=>{refreshes++};
const document={querySelector:()=>({insertAdjacentHTML:()=>{}})};
'''+function+'''
sendSessionToGarmin(0,{disabled:false,textContent:'Envoyer vers Garmin'}).then(()=>console.log(JSON.stringify({refreshes,pending:garminDeliveryPending.size})));
'''
    result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,encoding='utf-8',check=True)
    assert json.loads(result.stdout)=={'refreshes':1,'pending':0}

def test_uncertain_cached_state_blocks_stale_button_before_confirmation_or_network():
    html=dashboard.TEMPLATE_FILE.read_text(encoding='utf-8')
    function=re.search(r'  async function sendSessionToGarmin\(.*',html).group(0)
    script='''
const state={planProposal:{proposal:{sessions:[{session_id:'morning',date:'2099-10-13'}]}}};
const garminDeliveryPending=new Set(),deliveryFor=()=>({garmin_connect_status:'unknown',upload_status:'uncertain'});
const confirm=()=>{throw Error('confirmation forbidden')},fetch=()=>{throw Error('network forbidden')};
'''+function+'''
sendSessionToGarmin(0,{disabled:false,textContent:'stale button'}).then(()=>console.log(JSON.stringify({pending:garminDeliveryPending.size})));
'''
    result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,encoding='utf-8',check=True)
    assert json.loads(result.stdout)=={'pending':0}

def test_direct_journey_render_binds_new_cards_without_full_dashboard_reload():
    html=dashboard.TEMPLATE_FILE.read_text(encoding='utf-8')
    functions='\n'.join(re.search(r'  function '+name+r'\(.*',html).group(0)
        for name in ('showJourneyProduct','bindPlanSessionActions'))
    script='''
let buttons=[],calls=0;
const content={set innerHTML(value){buttons=[{dataset:{sessionId:'morning',sessionDecision:'accepted'},closest:()=>({querySelector:()=>({})})}]},querySelector:()=>null};
const dialog={open:false,showModal(){this.open=true}};
const $=id=>id==='journeyContent'?content:dialog,setJourneyPhase=()=>{};
const document={querySelectorAll:selector=>selector==='[data-session-decision]'?buttons:[]};
const decidePlanProposalV2=()=>{calls++};
'''+functions+'''
for(let n=0;n<2;n++){
 showJourneyProduct('<new cards>',{planPhase:true});
 if(typeof buttons[0].onclick!=='function')throw Error('direct render lost session consent handler');
 buttons[0].onclick();
}
console.log(JSON.stringify({calls}));
'''
    result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,encoding='utf-8',check=True)
    assert json.loads(result.stdout)=={'calls':2}
