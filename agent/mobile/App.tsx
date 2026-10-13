import React, {useEffect, useRef, useState} from 'react';
import {AppState, Alert, FlatList, Image, KeyboardAvoidingView, Platform, Pressable, StyleSheet, Text, TextInput, View} from 'react-native';
import {SafeAreaProvider, SafeAreaView} from 'react-native-safe-area-context';
import * as SecureStore from 'expo-secure-store';
import {createClient, mergeSnapshot, normalizeServer} from './client';

type ChatEvent = {id: number; role: string; kind: string; text: string; approval_id?: string; images?: {data_url: string; name: string}[]};
type Status = {session_id?: string; ready?: boolean; busy?: boolean; approval_id?: string; password_is_default?: boolean; configuration?: {provider?: string; model?: string}};
type Snapshot = {events: ChatEvent[]; cursor: number; status: Status};
const empty: Snapshot = {events: [], cursor: 0, status: {}};
const storageKey = 'veyra.connection';
function Button({title, onPress, disabled = false}: {title: string; onPress: () => void; disabled?: boolean}) {
  return <Pressable accessibilityRole="button" disabled={disabled} onPress={onPress} style={[styles.button, disabled && {opacity: .4}]}><Text style={styles.buttonText}>{title}</Text></Pressable>;
}
function Chat() {
  const [server, setServer] = useState('');
  const [password, setPassword] = useState('');
  const [connection, setConnection] = useState<{server: string; password: string} | null>(null);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState('');
  const [online, setOnline] = useState(false);
  const [posting, setPosting] = useState(false);
  const [draft, setDraft] = useState('');
  const [snapshot, setSnapshot] = useState<Snapshot>(empty);
  const snapshotRef = useRef<Snapshot>(empty);
  const postingRef = useRef(false);
  const list = useRef<FlatList>(null);
  const follow = useRef(true);
  useEffect(() => {
    let alive = true;
    SecureStore.getItemAsync(storageKey).then(value => {
      if (!alive || !value) return;
      const saved = JSON.parse(value);
      normalizeServer(saved.server);
      if (typeof saved.password !== 'string') return;
      setServer(saved.server); setPassword(saved.password); setConnection(saved);
    }).catch(() => {if (alive) setError('Could not restore connection. Enter it again.');})
      .finally(() => {if (alive) setLoading(false);});
    return () => {alive = false;};
  }, []);
  useEffect(() => {
    if (!connection) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    const request = createClient(connection.server, connection.password);
    async function poll() {
      if (stopped) return;
      if (AppState.currentState !== 'active') {timer = setTimeout(poll, 1000); return;}
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      let delay = 900;
      try {
        const data = await request('/api/events?after=' + snapshotRef.current.cursor, undefined, controller.signal);
        if (stopped) return;
        const next = mergeSnapshot(snapshotRef.current, data) as Snapshot;
        snapshotRef.current = next; setSnapshot(next); setOnline(true); setError('');
        if (data.has_more) delay = 0;
      } catch (err) {
        if (!stopped) {setOnline(false); setError((err as Error).message); delay = 2000;}
      } finally {
        clearTimeout(timeout);
        if (!stopped) timer = setTimeout(poll, delay);
      }
    }
    poll();
    return () => {stopped = true; clearTimeout(timer); controller?.abort();};
  }, [connection]);
  async function connect() {
    if (connecting) return;
    setConnecting(true); setError('');
    try {
      const address = normalizeServer(server);
      if (!password) throw new Error('Enter your Veyra network password.');
      const saved = {server: address, password};
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      try {await createClient(address, password)('/api/events?after=0', undefined, controller.signal);}
      finally {clearTimeout(timeout);}
      await SecureStore.setItemAsync(storageKey, JSON.stringify(saved));
      snapshotRef.current = empty; setSnapshot(empty); setConnection(saved); setOnline(true);
    } catch (err) {setError((err as Error).message);} finally {setConnecting(false);}
  }
  async function send(text: string, approvalId?: string) {
    if (!connection || postingRef.current) return;
    postingRef.current = true; setPosting(true); setError('');
    try {
      // Do not retry POST automatically: it may already have been accepted.
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {await createClient(connection.server, connection.password)('/api/messages', {text, approval_id: approvalId || null}, controller.signal);}
      finally {clearTimeout(timeout);}
      if (!approvalId) setDraft(current => current === text ? '' : current);
      follow.current = true;
    } catch (err) {Alert.alert('Message not confirmed', (err as Error).message + '\nCheck the conversation before retrying. Your draft is kept.');}
    finally {postingRef.current = false; setPosting(false);}
  }
  async function forget() {
    try {
      await SecureStore.deleteItemAsync(storageKey);
      setConnection(null); setPassword(''); setDraft(''); setSnapshot(empty); snapshotRef.current = empty; setOnline(false); setError('');
    } catch {Alert.alert('Could not forget connection', 'Please try again.');}
  }
  const available = online && snapshot.status.ready && !snapshot.status.busy && !posting;
  if (loading) return <Text style={styles.text}>Loading…</Text>;
  if (!connection) return <View style={styles.setup}>
    <Text style={styles.title}>Veyra</Text><Text style={styles.text}>Connect to the computer running Veyra. Use its private network IP, not localhost. Keep both devices on the same trusted Wi-Fi.</Text>
    <Text style={styles.text}>Server address</Text><TextInput accessibilityLabel="Server address" style={styles.input} value={server} onChangeText={setServer} autoCapitalize="none" autoCorrect={false} placeholder="http://192.168.1.20:3000" placeholderTextColor="#94a3b8" editable={!connecting}/>
    <Text style={styles.text}>Veyra network password (username: veyra)</Text><TextInput accessibilityLabel="Network password" style={styles.input} value={password} onChangeText={setPassword} secureTextEntry autoCapitalize="none" editable={!connecting}/>
    <Text style={styles.note}>This is not your ChatGPT password or API key. The initial network password is veyra; change it in web Settings. HTTP is unencrypted: never expose port 3000 to the internet. Connection details are saved in iOS secure storage.</Text>
    {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
    <Button title={connecting ? 'Connecting…' : 'Connect'} onPress={connect} disabled={connecting}/>
  </View>;
  return <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
    <View style={styles.header}><Text style={styles.title}>Veyra</Text><Button title="Forget connection" disabled={posting} onPress={() => Alert.alert('Forget this connection?', 'This removes saved connection details and the current draft from this phone. It does not sign out your model service on Veyra.', [{text: 'Cancel', style: 'cancel'}, {text: 'Forget', style: 'destructive', onPress: forget}])}/></View>
    <Text style={styles.note}>{snapshot.status.configuration?.provider || 'No service selected'} · {snapshot.status.configuration?.model || 'Choose a service in web Settings'}</Text>
    <Text style={styles.note}>{!online ? 'Reconnecting…' : !snapshot.status.ready ? 'Starting…' : snapshot.status.busy ? 'Veyra is working…' : 'Connected'}</Text>
    {snapshot.status.password_is_default && <Text style={styles.error}>Change the default network password in web Settings.</Text>}
    {!!error && <Text style={styles.error}>{error}</Text>}
    <FlatList<ChatEvent> ref={list} data={snapshot.events} keyExtractor={item => String(item.id)} style={styles.flex} contentContainerStyle={{paddingVertical: 12}} keyboardShouldPersistTaps="handled"
      onScroll={event => {const {layoutMeasurement, contentOffset, contentSize} = event.nativeEvent; follow.current = contentSize.height - contentOffset.y - layoutMeasurement.height < 100;}}
      onContentSizeChange={() => {if (follow.current) list.current?.scrollToEnd({animated: false});}}
      ListEmptyComponent={<Text style={styles.note}>This session’s messages appear here. Earlier sessions stay on the Veyra host.</Text>}
      renderItem={({item}) => <View style={[styles.message, item.role === 'user' && styles.user]}>
        <Text style={styles.label}>{item.role === 'user' ? 'You' : item.role === 'tool' ? 'Command output' : 'Veyra'}</Text>
        <Text selectable style={styles.text}>{item.text}</Text>
        {item.images?.filter(image => /^data:image\/(png|jpeg|webp|gif);base64,/.test(image.data_url)).map((image, index) => <Image key={index} source={{uri: image.data_url}} accessibilityLabel={image.name} style={styles.image} resizeMode="contain"/>)}
        {item.kind === 'approval' && <View style={styles.row}>
          <Button title="Approve commands" disabled={!available || item.approval_id !== snapshot.status.approval_id} onPress={() => Alert.alert('Run these commands?', item.text, [{text: 'Cancel', style: 'cancel'}, {text: 'Approve', onPress: () => send('/approve', item.approval_id)}])}/>
          <Button title="Deny" disabled={!available || item.approval_id !== snapshot.status.approval_id} onPress={() => send('/deny', item.approval_id)}/>
        </View>}
      </View>}/>
    <View style={styles.row}><TextInput accessibilityLabel="Message" style={[styles.input, styles.flex, {maxHeight: 130}]} multiline maxLength={20000} placeholder="Message Veyra…" placeholderTextColor="#94a3b8" value={draft} onChangeText={setDraft} editable={!posting}/><Button title="Send" disabled={!available || !draft.trim()} onPress={() => send(draft, /^(\/approve|\/deny|approve(?: these commands)?|deny(?: these commands)?)[.!]?$/i.test(draft.trim()) ? snapshot.status.approval_id : undefined)}/></View>
  </KeyboardAvoidingView>;
}
export default function App() {return <SafeAreaProvider><SafeAreaView style={styles.container}><Chat/></SafeAreaView></SafeAreaProvider>;}
const styles = StyleSheet.create({container: {flex: 1, backgroundColor: '#0f172a', paddingHorizontal: 16}, flex: {flex: 1}, setup: {gap: 12, paddingTop: 20}, header: {flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between'}, title: {color: '#fff', fontSize: 25, fontWeight: '700'}, text: {color: '#e2e8f0', fontSize: 16, lineHeight: 23}, note: {color: '#94a3b8', fontSize: 13, marginVertical: 6}, error: {color: '#fda4af', marginVertical: 6}, input: {color: '#fff', backgroundColor: '#1e293b', borderRadius: 10, padding: 12, fontSize: 16}, button: {backgroundColor: '#2563eb', borderRadius: 10, padding: 12, alignSelf: 'center'}, buttonText: {color: '#fff', fontWeight: '600'}, row: {flexDirection: 'row', gap: 10, alignItems: 'center', paddingVertical: 8}, message: {backgroundColor: '#1e293b', padding: 14, marginVertical: 5, borderRadius: 12}, user: {backgroundColor: '#163153'}, label: {color: '#93c5fd', fontWeight: '600', marginBottom: 8}, image: {width: '100%', height: 220, marginTop: 10}});
