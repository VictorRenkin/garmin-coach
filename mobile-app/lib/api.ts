import AsyncStorage from '@react-native-async-storage/async-storage';

export type Connection = { server: string; token: string };
export type Activity = { id: string; name: string; date: string; distanceKm?: number; durationSeconds?: number; paceSeconds?: number; averageHr?: number; analysis?: { titre?: string; synthese?: string; verdict?: string } };
export type Session = { date?: string; titre?: string; type?: string; duree_estimee_minutes?: number; distance_estimee_km?: number; statut?: string; objectif?: string };
export type CoachMessage = { id: string; role: 'user' | 'assistant'; content: string; status?: string };

const KEY = 'garmin-coach-connection-v1';
export async function loadConnection(): Promise<Connection | null> {
  const raw = await AsyncStorage.getItem(KEY);
  return raw ? JSON.parse(raw) : null;
}
export async function saveConnection(value: Connection) {
  const clean = { server: value.server.trim().replace(/\/$/, ''), token: value.token.trim() };
  await AsyncStorage.setItem(KEY, JSON.stringify(clean));
  return clean;
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const connection = await loadConnection();
  if (!connection) throw new Error('Configure d’abord la connexion au serveur.');
  const response = await fetch(`${connection.server}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', 'X-Garmin-Coach-Token': connection.token, ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || `Erreur ${response.status}`);
  return payload;
}
export const fmtPace = (seconds?: number) => seconds ? `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, '0')}/km` : '—';
