export async function getSession() {
  const res = await fetch('/api/session');
  return res.json();
}
