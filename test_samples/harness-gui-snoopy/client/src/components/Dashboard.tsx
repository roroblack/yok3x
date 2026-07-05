import React, { useState, useEffect } from 'react';
import { AlertCircle, Zap, RefreshCw, Pause, Play, Menu, X, Settings, FileText, Zap as ZapIcon, BookOpen } from 'lucide-react';
import LimitsGauge from './LimitsGauge';
import CoachPanel from './CoachPanel';
import RunsList from './RunsList';
import RunDetails from '../pages/RunDetails';
import TaskEditor from '../pages/TaskEditor';
import SettingsPage from '../pages/Settings';
import KnotViewer from '../pages/KnotViewer';

export default function Dashboard() {
  const [flavor, setFlavor] = useState('claude');
  const [guardEnabled, setGuardEnabled] = useState(true);
  const [currentTime, setCurrentTime] = useState(new Date());
  const [isWatching, setIsWatching] = useState(false);
  const [currentPage, setCurrentPage] = useState('dashboard');
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const limits: { [key: string]: { used_5h: number; used_7d: number; plan: string; status: 'ok' | 'warn' | 'stop' } } = {
    claude: { used_5h: 65, used_7d: 45, plan: 'plus', status: 'ok' },
    codex: { used_5h: 82, used_7d: 70, plan: 'max20', status: 'warn' },
    gemini: { used_5h: 45, used_7d: 30, plan: 'standard', status: 'ok' },
  };

  if (currentPage === 'run-details') {
    return <RunDetails onBack={() => setCurrentPage('dashboard')} />;
  }
  if (currentPage === 'task-editor') {
    return <TaskEditor onBack={() => setCurrentPage('dashboard')} />;
  }
  if (currentPage === 'settings') {
    return <SettingsPage onBack={() => setCurrentPage('dashboard')} />;
  }
  if (currentPage === 'knot') {
    return <KnotViewer onBack={() => setCurrentPage('dashboard')} />;
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Sidebar */}
      <div className={`fixed left-0 top-0 h-screen w-64 comic-panel p-4 z-50 transform transition-transform ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'} lg:translate-x-0 lg:relative lg:w-auto lg:h-auto lg:border-r-2 lg:border-b-0`}>
        <div className="flex items-center justify-between mb-6 lg:hidden">
          <h1 className="text-lg font-bold">Menu</h1>
          <button onClick={() => setSidebarOpen(false)} className="comic-panel p-1">
            <X size={20} />
          </button>
        </div>
        
        <nav className="space-y-2">
          <button
            onClick={() => { setCurrentPage('dashboard'); setSidebarOpen(false); }}
            className={`w-full text-left px-3 py-2 text-sm font-bold transition-colors flex items-center gap-2 ${currentPage === 'dashboard' ? 'bg-accent text-accent-foreground' : 'hover:bg-muted'}`}
          >
            <Zap size={16} /> Dashboard
          </button>
          <button
            onClick={() => { setCurrentPage('run-details'); setSidebarOpen(false); }}
            className={`w-full text-left px-3 py-2 text-sm font-bold transition-colors flex items-center gap-2 ${currentPage === 'run-details' ? 'bg-accent text-accent-foreground' : 'hover:bg-muted'}`}
          >
            <FileText size={16} /> Run Details
          </button>
          <button
            onClick={() => { setCurrentPage('task-editor'); setSidebarOpen(false); }}
            className={`w-full text-left px-3 py-2 text-sm font-bold transition-colors flex items-center gap-2 ${currentPage === 'task-editor' ? 'bg-accent text-accent-foreground' : 'hover:bg-muted'}`}
          >
            <ZapIcon size={16} /> Task Editor
          </button>
          <button
            onClick={() => { setCurrentPage('knot'); setSidebarOpen(false); }}
            className={`w-full text-left px-3 py-2 text-sm font-bold transition-colors flex items-center gap-2 ${currentPage === 'knot' ? 'bg-accent text-accent-foreground' : 'hover:bg-muted'}`}
          >
            <BookOpen size={16} /> Knowledge Graph
          </button>
          <button
            onClick={() => { setCurrentPage('settings'); setSidebarOpen(false); }}
            className={`w-full text-left px-3 py-2 text-sm font-bold transition-colors flex items-center gap-2 ${currentPage === 'settings' ? 'bg-accent text-accent-foreground' : 'hover:bg-muted'}`}
          >
            <Settings size={16} /> Settings
          </button>
        </nav>
      </div>

      {/* Main Content */}
      <div className="flex-1 p-4">
      {/* Top Bar */}
      <div className="comic-panel mb-6 p-4">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div className="flex items-center gap-4">
            <button
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="comic-panel p-2 lg:hidden hover:bg-muted transition-colors"
              title="Toggle menu"
            >
              <Menu size={20} />
            </button>
            <div>
              <h1 className="text-2xl font-bold">Harness Orchestrator</h1>
              <p className="text-sm text-muted-foreground">Flavor: <span className="font-bold text-accent">{flavor.toUpperCase()}</span></p>
            </div>
          </div>
          
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <label className="text-sm font-bold">Guard:</label>
              <button
                onClick={() => setGuardEnabled(!guardEnabled)}
                className={`comic-panel px-3 py-1 text-sm font-bold transition-colors ${
                  guardEnabled ? 'bg-accent text-accent-foreground' : 'bg-muted text-muted-foreground'
                }`}
              >
                {guardEnabled ? 'ON' : 'OFF'}
              </button>
            </div>
            
            <button className="comic-panel px-3 py-1 hover:bg-muted transition-colors" title="Refresh limits">
              <RefreshCw size={16} />
            </button>
            
            <div className="text-xs font-mono text-muted-foreground">
              {currentTime.toLocaleTimeString()}
            </div>
          </div>
        </div>
      </div>

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column: Limits & Coach */}
        <div className="lg:col-span-1 space-y-6">
          {/* Limits Gauge */}
          <LimitsGauge limits={limits} />
          
          {/* Coach Panel */}
          <CoachPanel limits={limits} guardEnabled={guardEnabled} />
        </div>

        {/* Right Column: Runs & Details */}
        <div className="lg:col-span-2">
          <div className="comic-panel p-4">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-bold">Recent Runs</h2>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setIsWatching(!isWatching)}
                  className={`comic-panel px-2 py-1 text-xs font-bold transition-colors ${
                    isWatching ? 'bg-accent text-accent-foreground' : 'bg-background'
                  }`}
                >
                  {isWatching ? <Pause size={14} /> : <Play size={14} />}
                </button>
                <span className="text-xs text-muted-foreground">
                  {isWatching ? 'Watching...' : 'Paused'}
                </span>
              </div>
            </div>
            
            <RunsList />
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}
