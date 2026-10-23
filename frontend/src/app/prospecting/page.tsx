import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { AppSession, readSignedPayload } from '../../lib/auth-server';
import ProspectingWorkspace from '../../components/ProspectingWorkspace';

export const dynamic = 'force-dynamic';
export default function ProspectingPage() {
  let session: AppSession | null = null;
  try { session = readSignedPayload<AppSession>(cookies().get('grokbot_session')?.value); } catch { session = null; }
  if (!session) redirect('/login');
  return <ProspectingWorkspace />;
}
