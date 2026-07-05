import React, { useState } from 'react';
import { ChevronLeft, Download, Copy } from 'lucide-react';

interface RunDetailsProps {
  runId?: string;
  onBack?: () => void;
  params?: { runId?: string };
}

export default function RunDetails({ runId, onBack, params }: RunDetailsProps) {
  const displayRunId = params?.runId || runId || 'run-001';
  const [logExpanded, setLogExpanded] = useState(false);

  const steps = [
    {
      index: 1,
      worker: 'claude-main',
      backend: 'claude_transcripts',
      status: 'done',
      score: 0.95,
      issues: [],
    },
    {
      index: 2,
      worker: 'codex-critic',
      backend: 'codex_appserver',
      status: 'done',
      score: 0.87,
      issues: ['Minor formatting issue'],
    },
    {
      index: 3,
      worker: 'claude-main',
      backend: 'claude_transcripts',
      status: 'running',
      score: null,
      issues: [],
    },
  ];

  const finalOutput = `# Task Completion Report

## Summary
- **Status**: In Progress
- **Pattern**: producer-reviewer
- **Started**: 2026-07-04 13:45:00 UTC
- **Duration**: 2 minutes 34 seconds

## Results
✓ Step 1: Producer generated initial response
✓ Step 2: Critic reviewed with score 0.87
→ Step 3: Producer refining based on feedback...

## Output
The orchestrated workflow is executing smoothly.
All guard limits are within acceptable ranges.`;

  const logContent = `[13:45:00] Starting run: run-001
[13:45:02] Executing step 1 with claude-main
[13:45:15] Step 1 completed with score 0.95
[13:45:16] Executing step 2 with codex-critic
[13:45:28] Step 2 completed with score 0.87
[13:45:29] Executing step 3 with claude-main
[13:47:34] Step 3 in progress...`;

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
            <h1 className="text-2xl font-bold">Run Details</h1>
            <p className="text-sm text-muted-foreground">ID: <span className="font-mono">{displayRunId}</span></p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Timeline */}
        <div className="lg:col-span-2">
          <div className="comic-panel p-4">
            <h2 className="text-lg font-bold mb-4">Step Timeline</h2>
            
            <div className="space-y-3">
              {steps.map((step) => (
                <div key={step.index} className="comic-panel p-3">
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex items-center gap-3">
                      <span className="font-bold text-lg">#{step.index}</span>
                      <div>
                        <p className="font-bold">{step.worker}</p>
                        <p className="text-xs text-muted-foreground">{step.backend}</p>
                      </div>
                    </div>
                    <div className="text-right">
                      {step.status === 'done' && (
                        <span className="text-xs font-bold text-green-600">✓ DONE</span>
                      )}
                      {step.status === 'running' && (
                        <span className="text-xs font-bold text-blue-600">⟳ RUNNING</span>
                      )}
                    </div>
                  </div>

                  {step.score !== null && (
                    <div className="mb-2">
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-xs font-bold">SCORE</span>
                        <span className="text-xs font-mono">{step.score.toFixed(2)}</span>
                      </div>
                      <div className="w-full h-2 border border-foreground bg-background">
                        <div
                          className="h-full bg-accent"
                          style={{ width: `${step.score * 100}%` }}
                        />
                      </div>
                    </div>
                  )}

                  {step.issues.length > 0 && (
                    <div className="mt-2 pt-2 border-t border-foreground">
                      <p className="text-xs font-bold text-yellow-600">⚠ Issues:</p>
                      <ul className="text-xs text-muted-foreground mt-1">
                        {step.issues.map((issue, i) => (
                          <li key={i}>• {issue}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Sidebar: Output & Log */}
        <div className="space-y-4">
          {/* Final Output */}
          <div className="comic-panel p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-bold">Output</h3>
              <button className="comic-panel p-1 hover:bg-muted" title="Copy">
                <Copy size={14} />
              </button>
            </div>
            <div className="bg-background border border-foreground p-2 text-xs font-mono max-h-40 overflow-auto">
              {finalOutput.split('\n').map((line, i) => (
                <div key={i}>{line}</div>
              ))}
            </div>
          </div>

          {/* Log Stream */}
          <div className="comic-panel p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-bold">Log</h3>
              <button className="comic-panel p-1 hover:bg-muted" title="Download">
                <Download size={14} />
              </button>
            </div>
            <div className="bg-background border border-foreground p-2 text-xs font-mono max-h-40 overflow-auto">
              {logContent.split('\n').map((line, i) => (
                <div key={i} className="text-muted-foreground">{line}</div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
