import { toast } from './toastService'

export async function api<T>(url: string, options?: RequestInit): Promise<T> {
  let response: Response
  try { response = await fetch(url, options) }
  catch (cause) { toast.error(cause); throw cause }
  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) {
    const failure = new Error(response.ok
      ? 'Web 页面已更新，但后端接口仍是旧版。请重启 python web_main.py 后刷新页面。'
      : `接口请求失败（${response.status}）；服务没有返回 JSON。`)
    toast.error(failure)
    throw failure
  }
  let payload: any
  try { payload = await response.json() }
  catch {
    const failure = new Error(`接口请求失败（${response.status}）；返回的 JSON 无法解析。`)
    toast.error(failure)
    throw failure
  }
  if (!response.ok) {
    const failure = new Error(payload?.detail || `请求失败（${response.status}）`)
    toast.error(failure)
    throw failure
  }
  return payload as T
}

export function writeOptions(method: string, data?: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json', 'X-Novel-Workbench': '1' },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }) }
}

export type Job = { id: string; kind: string; status: string; message: string;
  created_at?: string;
  events: { time: string; message: string; data: Record<string, unknown> }[];
  result: Record<string, unknown> | null }
