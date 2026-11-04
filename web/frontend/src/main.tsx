import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './app/App'
import './styles/global.css'

async function start() {
  const root = createRoot(document.getElementById('root')!)
  if (import.meta.env.MODE === 'mock') {
    try {
      const { startMock } = await import('./mock/start')
      await startMock()
    } catch (error) {
      console.error('Mock 初始化失败', error)
      root.render(<main role="alert">模拟服务未能启动，请检查本地开发环境。未连接真实服务。</main>)
      return
    }
  }
  root.render(<React.StrictMode><App mock={import.meta.env.MODE === 'mock'} /></React.StrictMode>)
}
void start()
