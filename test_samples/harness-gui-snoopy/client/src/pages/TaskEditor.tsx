import React, { useState } from 'react';
import { ChevronLeft, Save, Play, RotateCw } from 'lucide-react';

interface TaskEditorProps {
  onBack?: () => void;
}

export default function TaskEditor({ onBack }: TaskEditorProps = {}) {
  const [pattern, setPattern] = useState('producer-reviewer');
  const [task, setTask] = useState('Write a Python function to calculate fibonacci numbers');
  const [producer, setProducer] = useState('claude-main');
  const [reviewer, setReviewer] = useState('codex-critic');
  const [maxRounds, setMaxRounds] = useState(3);
  const [passScore, setPassScore] = useState(0.85);

  const patterns = ['producer-reviewer', 'pipeline', 'fanout-fanin'];
  const workers = ['claude-main', 'codex-main', 'codex-critic', 'gemini'];

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
            <h1 className="text-2xl font-bold">Task Editor</h1>
            <p className="text-sm text-muted-foreground">Create or edit a task configuration</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main Editor */}
        <div className="lg:col-span-2 space-y-4">
          {/* Pattern Selection */}
          <div className="comic-panel p-4">
            <label className="block text-sm font-bold mb-2">Pattern Type</label>
            <div className="flex gap-2">
              {patterns.map((p) => (
                <button
                  key={p}
                  onClick={() => setPattern(p)}
                  className={`comic-panel px-3 py-2 text-xs font-bold transition-colors ${
                    pattern === p
                      ? 'bg-accent text-accent-foreground'
                      : 'bg-background hover:bg-muted'
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>

          {/* Task Description */}
          <div className="comic-panel p-4">
            <label className="block text-sm font-bold mb-2">Task Description</label>
            <textarea
              value={task}
              onChange={(e) => setTask(e.target.value)}
              className="w-full h-24 p-2 border-2 border-foreground bg-background font-mono text-sm resize-none"
              placeholder="Enter task description..."
            />
          </div>

          {/* Pattern-Specific Config */}
          {pattern === 'producer-reviewer' && (
            <div className="space-y-4">
              {/* Producer */}
              <div className="comic-panel p-4">
                <label className="block text-sm font-bold mb-2">Producer Worker</label>
                <select
                  value={producer}
                  onChange={(e) => setProducer(e.target.value)}
                  className="w-full p-2 border-2 border-foreground bg-background font-mono text-sm"
                >
                  {workers.map((w) => (
                    <option key={w} value={w}>
                      {w}
                    </option>
                  ))}
                </select>
              </div>

              {/* Reviewer */}
              <div className="comic-panel p-4">
                <label className="block text-sm font-bold mb-2">Reviewer Worker</label>
                <select
                  value={reviewer}
                  onChange={(e) => setReviewer(e.target.value)}
                  className="w-full p-2 border-2 border-foreground bg-background font-mono text-sm"
                >
                  {workers.map((w) => (
                    <option key={w} value={w}>
                      {w}
                    </option>
                  ))}
                </select>
              </div>

              {/* Max Rounds */}
              <div className="comic-panel p-4">
                <label className="block text-sm font-bold mb-2">
                  Max Rounds: <span className="text-accent">{maxRounds}</span>
                </label>
                <input
                  type="range"
                  min="1"
                  max="10"
                  value={maxRounds}
                  onChange={(e) => setMaxRounds(Number(e.target.value))}
                  className="w-full"
                />
              </div>

              {/* Pass Score */}
              <div className="comic-panel p-4">
                <label className="block text-sm font-bold mb-2">
                  Pass Score: <span className="text-accent">{passScore.toFixed(2)}</span>
                </label>
                <input
                  type="range"
                  min="0.5"
                  max="1"
                  step="0.05"
                  value={passScore}
                  onChange={(e) => setPassScore(Number(e.target.value))}
                  className="w-full"
                />
              </div>
            </div>
          )}

          {pattern === 'pipeline' && (
            <div className="comic-panel p-4">
              <p className="text-sm text-muted-foreground">
                Pipeline configuration: Drag stages to reorder
              </p>
              <div className="mt-3 space-y-2">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="comic-panel p-2 bg-muted cursor-move">
                    <p className="text-xs font-bold">Stage {i}: {workers[i - 1]}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {pattern === 'fanout-fanin' && (
            <div className="comic-panel p-4">
              <p className="text-sm text-muted-foreground">
                Fanout-fanin configuration: Multiple workers in parallel
              </p>
              <div className="mt-3">
                <p className="text-xs font-bold mb-2">Workers:</p>
                <div className="space-y-1">
                  {workers.slice(0, 3).map((w) => (
                    <div key={w} className="text-xs text-muted-foreground">
                      • {w}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Sidebar: Actions */}
        <div className="space-y-4">
          <div className="comic-panel p-4">
            <h3 className="font-bold mb-3">Actions</h3>
            <div className="space-y-2">
              <button className="w-full comic-panel px-4 py-2 bg-accent text-accent-foreground font-bold text-sm hover:opacity-90 transition-opacity flex items-center justify-center gap-2">
                <Save size={16} />
                Save Task
              </button>
              <button className="w-full comic-panel px-4 py-2 bg-secondary text-secondary-foreground font-bold text-sm hover:opacity-90 transition-opacity flex items-center justify-center gap-2">
                <Play size={16} />
                Run Once
              </button>
              <button className="w-full comic-panel px-4 py-2 font-bold text-sm hover:bg-muted transition-colors flex items-center justify-center gap-2">
                <RotateCw size={16} />
                Loop (n times)
              </button>
            </div>
          </div>

          {/* Preview */}
          <div className="comic-panel p-4">
            <h3 className="font-bold mb-2">Preview</h3>
            <div className="bg-background border border-foreground p-2 text-xs font-mono max-h-40 overflow-auto">
              <p>task-{Date.now().toString().slice(-6)}.json</p>
              <p className="mt-2 text-muted-foreground">
                {`{
  "pattern": "${pattern}",
  "task": "${task.slice(0, 30)}...",
  "producer": "${producer}",
  "reviewer": "${reviewer}"
}`}
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
