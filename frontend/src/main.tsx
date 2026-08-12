import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { StartupGate } from './components/StartupGate'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <StartupGate runtime={window.aetherRuntime}>
      <ErrorBoundary
        scope="AetherChat"
        fallbackClassName="m-6 rounded-lg border border-[#d9b6af] bg-[#f8ece9] p-6 text-[#67372f]"
      >
        <App />
      </ErrorBoundary>
    </StartupGate>
  </React.StrictMode>,
)
