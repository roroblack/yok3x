import React, { useState } from 'react';
import { ChevronLeft, Save } from 'lucide-react';

interface SettingsProps {
  onBack?: () => void;
}

export default function Settings({ onBack }: SettingsProps = {}) {
  const [guardEnabled, setGuardEnabled] = useState(true);
  const [softRatio, setSoftRatio] = useState(0.8);
  const [hardRatio, setHardRatio] = useState(1.0);
  const [useRealLimits, setUseRealLimits] = useState(true);
  const [flavor, setFlavor] = useState('claude');
  const [activeTab, setActiveTab] = useState('guard');

  const tabs = ['guard', 'limits', 'workers', 'backends'];

  return (
    <div className="min-h-screen bg-background text-foreground p-4">
      {/* Header */}
      <div className="comic-panel mb-6 p-4">
        <div className="flex items-center gap-4">
          {onBack && (
            <button
              onClick={onBack}
              className="comic-panel p-2 hover:bg-muted transition-colors"
              title="Back"
            >
              <ChevronLeft size={20} />
            </button>
          )}
          <div>
            <h1 className="text-2xl font-bold">Settings</h1>
            <p className="text-sm text-muted-foreground">Configure Harness behavior and limits</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Tab Navigation */}
        <div className="lg:col-span-1">
          <div className="comic-panel p-4 space-y-2">
            {tabs.map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`w-full text-left px-3 py-2 text-sm font-bold transition-colors ${
                  activeTab === tab
                    ? 'bg-accent text-accent-foreground'
                    : 'hover:bg-muted'
                }`}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
              </button>
            ))}
          </div>
        </div>

        {/* Content */}
        <div className="lg:col-span-3 space-y-4">
          {/* Guard Settings */}
          {activeTab === 'guard' && (
            <>
              <div className="comic-panel p-4">
                <div className="flex items-center justify-between mb-4">
                  <label className="text-sm font-bold">Guard Enabled</label>
                  <button
                    onClick={() => setGuardEnabled(!guardEnabled)}
                    className={`comic-panel px-3 py-1 text-sm font-bold transition-colors ${
                      guardEnabled ? 'bg-accent text-accent-foreground' : 'bg-muted'
                    }`}
                  >
                    {guardEnabled ? 'ON' : 'OFF'}
                  </button>
                </div>
              </div>

              <div className="comic-panel p-4">
                <label className="block text-sm font-bold mb-3">
                  Soft Limit (Warning): <span className="text-accent">{(softRatio * 100).toFixed(0)}%</span>
                </label>
                <input
                  type="range"
                  min="0.5"
                  max="0.99"
                  step="0.05"
                  value={softRatio}
                  onChange={(e) => setSoftRatio(Number(e.target.value))}
                  className="w-full"
                  disabled={!guardEnabled}
                />
                <p className="text-xs text-muted-foreground mt-2">
                  When usage exceeds this threshold, warnings are triggered.
                </p>
              </div>

              <div className="comic-panel p-4">
                <label className="block text-sm font-bold mb-3">
                  Hard Limit (Block): <span className="text-accent">{(hardRatio * 100).toFixed(0)}%</span>
                </label>
                <input
                  type="range"
                  min="0.8"
                  max="1.0"
                  step="0.05"
                  value={hardRatio}
                  onChange={(e) => setHardRatio(Number(e.target.value))}
                  className="w-full"
                  disabled={!guardEnabled}
                />
                <p className="text-xs text-muted-foreground mt-2">
                  When usage exceeds this threshold, calls are blocked.
                </p>
              </div>
            </>
          )}

          {/* Limits Settings */}
          {activeTab === 'limits' && (
            <>
              <div className="comic-panel p-4">
                <div className="flex items-center justify-between mb-4">
                  <label className="text-sm font-bold">Use Real Limits</label>
                  <button
                    onClick={() => setUseRealLimits(!useRealLimits)}
                    className={`comic-panel px-3 py-1 text-sm font-bold transition-colors ${
                      useRealLimits ? 'bg-accent text-accent-foreground' : 'bg-muted'
                    }`}
                  >
                    {useRealLimits ? 'ON' : 'OFF'}
                  </button>
                </div>
              </div>

              <div className="comic-panel p-4">
                <label className="block text-sm font-bold mb-2">Plan Selection</label>
                <div className="space-y-2">
                  {['claude', 'codex', 'gemini'].map((tool) => (
                    <div key={tool} className="flex items-center justify-between">
                      <span className="text-sm font-bold uppercase">{tool}</span>
                      <select className="p-1 border-2 border-foreground bg-background text-sm font-mono">
                        <option>plus</option>
                        <option>max20</option>
                        <option>standard</option>
                      </select>
                    </div>
                  ))}
                </div>
              </div>

              <div className="comic-panel p-4">
                <label className="block text-sm font-bold mb-2">Daily Budget (USD)</label>
                <div className="space-y-2">
                  {['claude', 'codex', 'gemini'].map((tool) => (
                    <div key={tool} className="flex items-center gap-2">
                      <span className="text-sm font-bold uppercase w-16">{tool}</span>
                      <input
                        type="number"
                        defaultValue="10"
                        className="flex-1 p-1 border-2 border-foreground bg-background text-sm font-mono"
                      />
                      <span className="text-xs text-muted-foreground">USD/day</span>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* Workers Settings */}
          {activeTab === 'workers' && (
            <>
              <div className="comic-panel p-4">
                <label className="block text-sm font-bold mb-3">Orchestrator Flavor</label>
                <div className="flex gap-2">
                  {['claude', 'codex', 'gemini'].map((f) => (
                    <button
                      key={f}
                      onClick={() => setFlavor(f)}
                      className={`comic-panel px-3 py-2 text-xs font-bold transition-colors ${
                        flavor === f
                          ? 'bg-accent text-accent-foreground'
                          : 'bg-background hover:bg-muted'
                      }`}
                    >
                      {f.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>

              <div className="comic-panel p-4">
                <label className="block text-sm font-bold mb-2">Worker Configuration</label>
                <div className="space-y-2">
                  {['claude-main', 'codex-main', 'codex-critic', 'gemini'].map((worker) => (
                    <div key={worker} className="comic-panel p-2">
                      <p className="text-xs font-bold">{worker}</p>
                      <p className="text-xs text-muted-foreground mt-1">
                        Backend: {worker.includes('claude') ? 'claude_transcripts' : 'codex_appserver'}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* Backends Settings */}
          {activeTab === 'backends' && (
            <>
              <div className="comic-panel p-4">
                <label className="block text-sm font-bold mb-2">Backend Probes</label>
                <div className="space-y-2">
                  {[
                    { name: 'Claude Transcripts', type: 'claude_transcripts', timeout: 30 },
                    { name: 'Codex AppServer', type: 'codex_appserver', timeout: 20 },
                    { name: 'Ledger', type: 'ledger', timeout: 5 },
                  ].map((backend) => (
                    <div key={backend.type} className="comic-panel p-2">
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="text-xs font-bold">{backend.name}</p>
                          <p className="text-xs text-muted-foreground font-mono">{backend.type}</p>
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {backend.timeout}s timeout
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* Save Button */}
          <button className="w-full comic-panel px-4 py-3 bg-accent text-accent-foreground font-bold hover:opacity-90 transition-opacity flex items-center justify-center gap-2">
            <Save size={16} />
            Save Settings
          </button>
        </div>
      </div>
    </div>
  );
}
