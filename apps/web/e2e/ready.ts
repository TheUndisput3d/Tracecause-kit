export default async function ready() {
  for (let attempt=0;attempt<50;attempt++) {
    try {
      const [web,api] = await Promise.all([fetch('http://127.0.0.1:15173'),fetch('http://127.0.0.1:18080/health/ready')])
      if (web.ok && api.ok) return
    } catch { /* Startup connection refusal is retried within this bounded gate. */ }
    await new Promise((resolve) => setTimeout(resolve,200))
  }
  throw new Error('Real dashboard services did not become ready within ten seconds.')
}
