"""Seven isolated multi-turn hypotheses; only --real calls the existing provider."""
from __future__ import annotations
import argparse,json,sys,tempfile
from pathlib import Path
from datetime import date,timedelta
from dataclasses import replace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from garmin_coach import config,dashboard,coach_chat,history_import,history_storage,sync,athlete_memory

REFERENCE=date(2026,10,15)
IMPORT_REFERENCE=date(2026,10,8)
PROFILES={
 'beginner':(['running']*2,'Je préfère courir le mardi et le samedi. Compare mes deux dernières semaines enregistrées en donnant le nombre de séances, les disciplines et les limites. Ne change rien.','Quels jours ai-je dit préférer ? Explique comment tenir compte de ma régularité observée, sans créer de plan.'),
 'confirmed':(['running']*5,'Compare ma fréquence et mon volume enregistrés sur 28 et 84 jours, puis les deux semaines récentes. Ne crée aucune action.','Peux-tu conclure que je suis fatigué ou en progression grâce à ces données seules ? Justifie et distingue les faits de ce qui manque.'),
 'very_active':(['running','strength_training','running']*7,'Combien de séances sont enregistrées dans chacune des deux dernières semaines ? La liste est-elle complète ? Distingue les sports sans convertir la musculation en kilomètres.','Cette fréquence prouve-t-elle que ma récupération est bonne ? Réponds avec les limites des mesures disponibles, sans changer le plan.'),
 'triathlon':(['running','cycling','lap_swimming']*2,'Compare mes deux semaines récentes par discipline et cite les volumes enregistrés de course, vélo et natation. Ne propose aucune modification.','Voir mes trois disciplines signifie-t-il que Motionest peut publier automatiquement un plan natation sur Garmin ? Explique prudemment ses limites, sans créer ni publier de plan.'),
 'old':([], 'Analyse mes entraînements de juin 2025 en citant la période réellement consultée et les faits disponibles.','Ces anciennes activités établissent-elles mon niveau actuel ? Explique avec les données récentes disponibles, sans inventer.'),
 'no_hr':(['running']*3,'Compare mes deux semaines et précise quelles mesures cardiaques sont réellement disponibles. Ne crée aucune action.','Peux-tu fixer des zones cardiaques fiables avec ces données ? Explique ce qui est établi et ce qui ne l’est pas, sans inventer ma fréquence maximale.'),
 'incomplete':(['running','cycling','strength_training'],'Décris mes deux semaines enregistrées en séparant les mesures présentes des mesures manquantes. Ne change rien.','Analyse mes activités du 2026-09-01 au 2026-09-30. L’import incomplet permet-il de conclure à une absence d’entraînement ?'),
}

class FakeGarmin:
 def __init__(self,rows):self.rows=sorted(rows,key=lambda r:r['startTimeLocal'],reverse=True);self.calls=[]
 def get_activities(self,start,limit):self.calls.append([start,limit]);return self.rows[start:start+limit]

def seed(name,sports):
 history_import.initialize_history_import('all',reference_date=IMPORT_REFERENCE)
 rows=[]
 for week in range(12):
  for slot,sport in enumerate(sports):
   day=REFERENCE-timedelta(days=week*7+slot*7//len(sports)+1)
   distance={'running':6000,'cycling':25000,'lap_swimming':1500}.get(sport,0)
   a={'activityId':f'PRIVATE-ID-{name}-{week}-{slot}','activityName':'PRIVATE TITLE',
      'ownerEmail':'private-synthetic@example.com','latitude':50,
      'activityType':{'typeKey':sport},'startTimeLocal':f'{day}T{7+slot%3:02d}:00:00',
      'distance':distance,'movingDuration':1800,'elevationGain':20}
   if name not in ('no_hr','incomplete'):a.update(averageHR=135,maxHR=160,activityTrainingLoad=40,aerobicTrainingEffect=2.5)
   if sport=='cycling':a['averagePower']=180
   if name=='incomplete' and slot%2==0:a.pop('movingDuration');a.pop('distance')
   rows.append(a)
 if name=='old':
  rows=[{'activityId':f'PRIVATE-ID-old-{i}','activityName':'PRIVATE TITLE','activityType':{'typeKey':'running'},'startTimeLocal':f'2025-06-{day:02d}T08:00:00','distance':distance,'movingDuration':duration,'isPR':True} for i,(day,distance,duration) in enumerate([(5,5000,1500),(12,10000,3300),(20,12000,4200)])]
 else:
  sport='cycling' if name=='triathlon' else 'running'
  a={'activityId':f'PRIVATE-ID-{name}-new','activityType':{'typeKey':sport},'startTimeLocal':f'{REFERENCE}T12:00:00','distance':10000 if sport=='cycling' else 4000,'movingDuration':1800}
  if name not in ('no_hr','incomplete'):a.update(averageHR=132,activityTrainingLoad=25)
  rows.append(a)
 for row in rows:
  if row['startTimeLocal'][:10]<=str(IMPORT_REFERENCE):history_storage.save_activity_metadata(row)
 history_import.update_history_import({'status':'interrupted' if name=='incomplete' else 'complete','pagination_complete':name!='incomplete'})
 client=FakeGarmin(rows)
 sync.sync_once(client,bootstrap=True)
 return client

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--real',action='store_true');parser.add_argument('--env',type=Path);parser.add_argument('--profile',choices=PROFILES);parser.add_argument('--output',type=Path,default=Path('reports/conversations-2a.json'));args=parser.parse_args()
 if not args.real:raise SystemExit('Use --real explicitly.')
 if args.env:load_dotenv(args.env,override=True)
 settings=replace(config.load_settings(),email=None,password=None)
 if not settings.ai_api_key:raise SystemExit('AI key unavailable; no requests made.')
 report={'reference_date':str(REFERENCE),'synthetic_only':True,'real_garmin_calls':0,'api_attempts':0,'profiles':[]}
 captured=[]; original_client=coach_chat.OpenAI
 class Responses:
  def __init__(self,inner):self.inner=inner
  def parse(self,**kwargs):
   payload=json.loads(kwargs['input']); encoded=json.dumps(payload)
   assert all(s not in encoded for s in ['PRIVATE-ID-','PRIVATE TITLE','private-synthetic@example.com','latitude'])
   captured.append(payload);report['api_attempts']+=1
   return self.inner.parse(**kwargs)
 class Client:
  def __init__(self,**kwargs):self.responses=Responses(original_client(**kwargs).responses)
 coach_chat.OpenAI=Client
 def save():
  args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 with tempfile.TemporaryDirectory(prefix='motionest-2a-ia-') as temp:
  root=Path(temp);config.GLOBAL_DATA_DIR=root;config.USERS_DATA_DIR=root/'users';config.SECRETS_DIR=root/'secrets'
  for name,(sports,first,second) in PROFILES.items():
   if args.profile and name!=args.profile:continue
   item={'name':name,'turns':[]};report['profiles'].append(item)
   with config.user_context(name):
    client=seed(name,sports);ctx=dashboard.coach_context();multi=ctx['contexte_multisport']
    item.update(fake_fetch_pages=client.calls,reference_date=multi.get('reference_date'),counts={k:w['summary']['activity_count'] for k,w in multi.get('weeks',{}).items()},windows={k:v['activity_count'] for k,v in multi.get('history',{}).get('windows',{}).items()},truncation=multi.get('budget'))
    for prompt in (first,second):
     request=coach_chat.submit_message(prompt);ctx=dashboard.coach_context();before=len(captured);turn={'prompt':prompt,'selected_memory':len(ctx.get('memoire_pertinente',[]))}
     try:
      answer=coach_chat.generate_response(request,settings,ctx);coach_chat.store_response(request['id'],answer)
      turn.update(message=answer.message,action=answer.action,conversation_state=answer.conversation_state,real_call=len(captured)>before,persistent_memory_count=len(athlete_memory.load_memory(include_inactive=False)))
      if len(captured)>before:turn['search']=captured[-1]['contexte'].get('recherche_historique')
     except Exception as exc:
      turn['error_type']=type(exc).__name__;coach_chat.mark_request_failed(request['id'])
     item['turns'].append(turn);save();print(f'{name} turn{len(item["turns"])} {turn.get("error_type","ok")}',flush=True)
    usage=root/'users'/name/'ai_usage.json';item['usage']=json.loads(usage.read_text()) if usage.exists() else []
    save()
 report['input_tokens']=sum(e['input_tokens'] for p in report['profiles'] for e in p.get('usage',[]));report['output_tokens']=sum(e['output_tokens'] for p in report['profiles'] for e in p.get('usage',[]));report['recorded_total_tokens']=report['input_tokens']+report['output_tokens'];save()
 print(json.dumps({k:v for k,v in report.items() if k!='profiles'}))
 if any('error_type' in t for p in report['profiles'] for t in p['turns']):raise SystemExit(1)
if __name__=='__main__':main()
