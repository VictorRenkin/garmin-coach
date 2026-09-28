import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Alert, FlatList, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Activity, api, CoachMessage, Connection, fmtPace, loadConnection, saveConnection, Session } from '../lib/api';

type Tab = 'Accueil' | 'Plan' | 'Coach' | 'Réglages';
const colors = { bg: '#07120f', card: '#10231d', card2: '#153229', green: '#65e6a7', text: '#f2fff8', muted: '#9ab4a8', coral: '#ff846f' };

export default function App() {
  const [tab, setTab] = useState<Tab>('Accueil');
  const [connection, setConnection] = useState<Connection>({ server: '', token: '' });
  const [activities, setActivities] = useState<Activity[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [messages, setMessages] = useState<CoachMessage[]>([]);
  const [objective, setObjective] = useState<any>(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    if (!connection.server) return;
    setBusy(true); setError('');
    try {
      const [a, p, m, profile] = await Promise.all([
        api<{ activities: Activity[] }>('/api/activities'), api<{ plan: { seances?: Session[] } | null }>('/api/plan'),
        api<{ messages: CoachMessage[] }>('/api/coach/messages'), api<{ objective: any }>('/api/profile'),
      ]);
      setActivities(a.activities.slice().reverse()); setSessions(p.plan?.seances || []); setMessages(m.messages); setObjective(profile.objective);
    } catch (e) { setError(e instanceof Error ? e.message : 'Connexion impossible'); }
    finally { setBusy(false); }
  }, [connection.server, connection.token]);

  useEffect(() => { loadConnection().then(c => { if (c) setConnection(c); }); }, []);
  useEffect(() => { if (connection.server) refresh(); }, [connection.server, connection.token]);

  async function connect() {
    try { const saved = await saveConnection(connection); setConnection(saved); await api('/api/mobile/health'); Alert.alert('Connecté', 'Le téléphone communique avec Garmin Coach.'); refresh(); }
    catch (e) { Alert.alert('Connexion impossible', e instanceof Error ? e.message : 'Vérifie les informations.'); }
  }
  async function sync() {
    setBusy(true); try { const r = await api<{ message: string }>('/api/sync', { method: 'POST', body: '{}' }); Alert.alert('Synchronisation', r.message); await refresh(); }
    catch (e) { Alert.alert('Erreur', e instanceof Error ? e.message : 'Synchronisation impossible'); } finally { setBusy(false); }
  }
  async function send() {
    if (!draft.trim()) return; const text = draft; setDraft(''); setBusy(true);
    try { await api('/api/coach/messages', { method: 'POST', body: JSON.stringify({ message: text }) }); await refresh(); }
    catch (e) { Alert.alert('Coach indisponible', e instanceof Error ? e.message : 'Erreur'); } finally { setBusy(false); }
  }

  const latest = activities[0];
  return <SafeAreaView style={s.safe}>
    <View style={s.header}><View><Text style={s.brand}>GARMIN COACH</Text><Text style={s.subtitle}>{objective?.title || 'Ton entraînement, plus clair'}</Text></View><View style={[s.dot, { backgroundColor: error ? colors.coral : colors.green }]} /></View>
    {error ? <Pressable onPress={() => setTab('Réglages')} style={s.error}><Text style={s.errorText}>{error} · Ouvrir les réglages</Text></Pressable> : null}
    {tab === 'Accueil' && <ScrollView refreshControl={<RefreshControl refreshing={busy} onRefresh={refresh} tintColor={colors.green} />} contentContainerStyle={s.content}>
      <View style={s.hero}><Text style={s.eyebrow}>DERNIÈRE COURSE</Text><Text style={s.big}>{latest?.distanceKm ?? '—'} <Text style={s.unit}>km</Text></Text><Text style={s.muted}>{latest ? `${fmtPace(latest.paceSeconds)}  ·  ${latest.averageHr || '—'} bpm` : 'Synchronise Garmin pour commencer'}</Text><Pressable style={s.primary} onPress={sync} disabled={busy}><Text style={s.primaryText}>{busy ? 'Synchronisation…' : 'Actualiser Garmin'}</Text></Pressable></View>
      {latest?.analysis && <View style={s.card}><Text style={s.eyebrow}>ANALYSE DU COACH</Text><Text style={s.cardTitle}>{latest.analysis.titre || latest.analysis.verdict}</Text><Text style={s.body}>{latest.analysis.synthese}</Text></View>}
      <Text style={s.section}>Activités récentes</Text>{activities.slice(0, 5).map(a => <View style={s.row} key={a.id}><View><Text style={s.rowTitle}>{a.name}</Text><Text style={s.muted}>{a.date.slice(0, 10)}</Text></View><View style={s.alignRight}><Text style={s.rowValue}>{a.distanceKm} km</Text><Text style={s.muted}>{fmtPace(a.paceSeconds)}</Text></View></View>)}
    </ScrollView>}
    {tab === 'Plan' && <FlatList contentContainerStyle={s.content} data={sessions} keyExtractor={(_, i) => String(i)} ListEmptyComponent={<Text style={s.empty}>Aucun plan actif. Synchronise Garmin depuis l’accueil.</Text>} ListHeaderComponent={<Text style={s.section}>Plan de la semaine</Text>} renderItem={({ item, index }) => <View style={s.session}><View style={s.index}><Text style={s.indexText}>{index + 1}</Text></View><View style={s.flex}><Text style={s.rowTitle}>{item.titre || item.type || 'Séance'}</Text><Text style={s.muted}>{item.date || 'Date à définir'} · {item.duree_estimee_minutes || '—'} min · {item.distance_estimee_km || '—'} km</Text>{item.objectif ? <Text style={s.body}>{item.objectif}</Text> : null}</View></View>} />}
    {tab === 'Coach' && <View style={s.flex}><FlatList contentContainerStyle={s.content} data={messages} keyExtractor={m => m.id} ListHeaderComponent={<><Text style={s.section}>Conversation</Text><Text style={s.muted}>Les réponses utilisent la clé OpenAI conservée sur ton ordinateur.</Text></>} renderItem={({ item }) => <View style={[s.bubble, item.role === 'user' ? s.userBubble : s.aiBubble]}><Text style={s.body}>{item.content}</Text></View>} /><View style={s.composer}><TextInput style={s.input} value={draft} onChangeText={setDraft} placeholder="Demande au coach…" placeholderTextColor={colors.muted} multiline /><Pressable style={s.send} onPress={send}><Text style={s.sendText}>↑</Text></Pressable></View></View>}
    {tab === 'Réglages' && <ScrollView contentContainerStyle={s.content}><Text style={s.section}>Connexion locale</Text><Text style={s.muted}>L’ordinateur et le téléphone doivent être sur le même Wi-Fi. Lance « demarrer-app-mobile.cmd » sur l’ordinateur puis recopie les deux valeurs affichées.</Text><Text style={s.label}>Adresse du serveur</Text><TextInput style={s.field} autoCapitalize="none" autoCorrect={false} value={connection.server} onChangeText={server => setConnection(c => ({ ...c, server }))} placeholder="http://192.168.1.20:8765" placeholderTextColor={colors.muted} /><Text style={s.label}>Jeton mobile</Text><TextInput style={s.field} autoCapitalize="none" autoCorrect={false} secureTextEntry value={connection.token} onChangeText={token => setConnection(c => ({ ...c, token }))} placeholder="Jeton affiché sur le PC" placeholderTextColor={colors.muted} /><Pressable style={s.primary} onPress={connect}><Text style={s.primaryText}>Enregistrer et tester</Text></Pressable><View style={s.card}><Text style={s.cardTitle}>Sécurité</Text><Text style={s.body}>La clé OpenAI et la session Garmin restent sur l’ordinateur. L’app mobile reçoit uniquement les données sportives nécessaires.</Text></View></ScrollView>}
    <View style={s.nav}>{(['Accueil', 'Plan', 'Coach', 'Réglages'] as Tab[]).map(item => <Pressable key={item} onPress={() => setTab(item)} style={s.navItem}><Text style={[s.navText, tab === item && s.navActive]}>{item}</Text></Pressable>)}</View>
  </SafeAreaView>;
}

const s = StyleSheet.create({ safe:{flex:1,backgroundColor:colors.bg},flex:{flex:1},header:{paddingHorizontal:20,paddingVertical:16,flexDirection:'row',alignItems:'center',justifyContent:'space-between'},brand:{color:colors.green,fontSize:12,fontWeight:'900',letterSpacing:2},subtitle:{color:colors.text,fontSize:20,fontWeight:'700',marginTop:4},dot:{width:10,height:10,borderRadius:5},content:{padding:20,paddingBottom:110,gap:14},hero:{backgroundColor:colors.card2,borderRadius:26,padding:24},eyebrow:{color:colors.green,fontSize:11,fontWeight:'800',letterSpacing:1.5},big:{color:colors.text,fontSize:58,fontWeight:'800',marginTop:12},unit:{fontSize:22,color:colors.muted},muted:{color:colors.muted,fontSize:13,lineHeight:20},primary:{backgroundColor:colors.green,borderRadius:16,padding:16,alignItems:'center',marginTop:18},primaryText:{color:'#062116',fontWeight:'900'},card:{backgroundColor:colors.card,borderRadius:20,padding:18,gap:8},cardTitle:{color:colors.text,fontSize:18,fontWeight:'800'},body:{color:colors.text,fontSize:14,lineHeight:21},section:{color:colors.text,fontSize:24,fontWeight:'800',marginTop:8},row:{backgroundColor:colors.card,borderRadius:18,padding:16,flexDirection:'row',justifyContent:'space-between'},rowTitle:{color:colors.text,fontSize:15,fontWeight:'700'},rowValue:{color:colors.green,fontSize:16,fontWeight:'800'},alignRight:{alignItems:'flex-end'},session:{backgroundColor:colors.card,borderRadius:20,padding:16,flexDirection:'row',gap:14},index:{width:34,height:34,borderRadius:17,backgroundColor:colors.green,alignItems:'center',justifyContent:'center'},indexText:{fontWeight:'900',color:'#062116'},empty:{color:colors.muted,textAlign:'center',marginTop:80},bubble:{maxWidth:'88%',padding:14,borderRadius:18,marginVertical:5},userBubble:{backgroundColor:'#245944',alignSelf:'flex-end'},aiBubble:{backgroundColor:colors.card,alignSelf:'flex-start'},composer:{padding:12,flexDirection:'row',gap:10,borderTopWidth:1,borderTopColor:'#1d3b31'},input:{flex:1,backgroundColor:colors.card,color:colors.text,borderRadius:18,paddingHorizontal:16,paddingVertical:12,maxHeight:100},send:{width:48,height:48,borderRadius:24,backgroundColor:colors.green,alignItems:'center',justifyContent:'center'},sendText:{fontSize:25,fontWeight:'900',color:'#062116'},label:{color:colors.text,fontWeight:'700',marginTop:10},field:{backgroundColor:colors.card,color:colors.text,borderRadius:14,padding:15},error:{marginHorizontal:20,backgroundColor:'#542922',padding:10,borderRadius:10},errorText:{color:'#ffd6ce',fontSize:12},nav:{position:'absolute',bottom:0,left:0,right:0,height:72,backgroundColor:'#0b1b16',borderTopWidth:1,borderTopColor:'#1d3b31',flexDirection:'row',paddingBottom:10},navItem:{flex:1,alignItems:'center',justifyContent:'center'},navText:{color:colors.muted,fontSize:12,fontWeight:'700'},navActive:{color:colors.green}});
