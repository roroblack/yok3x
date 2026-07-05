import React, { useState } from 'react';
import { ChevronDown, ChevronRight, AlertCircle, CheckCircle, Clock, XCircle } from 'lucide-react';

interface Run {
  id: string;
  pattern: string;
  status: 'running' | 'done' | 'aborted' | 'stopped_by_guard';
  step: number;
  totalSteps: number;
  lastWorker: string;
  timestamp: string;
}

export default function RunsList() {
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  const runs: Run[] = [
    {
      id: 'run-001',
      pattern: 'producer-reviewer',
      status: 'running',
      step: 3,
      totalSteps: 5,
      lastWorker: 'claude-main',
      timestamp: '2 min ago',
    },
    {
      id: 'run-002',
      pattern: 'pipeline',
      status: 'done',
      step: 8,
      totalSteps: 8,
      lastWorker: 'codex-critic',
      timestamp: '15 min ago',
    },
    {
      id: 'run-003',
      pattern: 'fanout-fanin',
      status: 'stopped_by_guard',
      step: 2,
      totalSteps: 6,
      lastWorker: 'gemini',
      timestamp: '1 hour ago',
    },
  ];

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'running':
        return <Clock size={14} className="text-blue-600 animate-spin" />;
      case 'done':
        return <CheckCircle size={14} className="text-green-600" />;
      case 'stopped_by_guard':
        return <AlertCircle size={14} className="text-red-600" />;
      case 'aborted':
        return <XCircle size={14} className="text-gray-600" />;
      default:
        return null;
    }
  };

  const getStatusLabel = (status: string) => {
    switch (status) {
      case 'running':
        return 'Running';
      case 'done':
        return 'Done';
      case 'stopped_by_guard':
        return 'Blocked';
      case 'aborted':
        return 'Aborted';
      default:
        return status;
    }
  };

  const steps = [
    { index: 1, worker: 'claude-main', status: 'done', score: 0.95 },
    { index: 2, worker: 'codex-critic', status: 'done', score: 0.87 },
    { index: 3, worker: 'claude-main', status: 'running', score: null },
  ];

  return (
    <div className="space-y-2">
      {runs.map((run) => (
        <div key={run.id}>
          {/* Run Row */}
          <button
            onClick={() => setExpandedRun(expandedRun === run.id ? null : run.id)}
            className="w-full comic-panel p-3 hover:bg-muted transition-colors text-left"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3 flex-1">
                {expandedRun === run.id ? (
                  <ChevronDown size={16} />
                ) : (
                  <ChevronRight size={16} />
                )}
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    {getStatusIcon(run.status)}
                    <span className="font-bold text-sm">{run.id}</span>
                    <span className="text-xs bg-background px-2 py-0.5">
                      {run.pattern}
                    </span>
                  </div>
                  <div className="text-xs text-muted-foreground mt-1">
                    Step {run.step}/{run.totalSteps} • {run.lastWorker} • {run.timestamp}
                  </div>
                </div>
              </div>
              <span className="text-xs font-bold px-2 py-1 bg-background">
                {getStatusLabel(run.status)}
              </span>
            </div>
          </button>

          {/* Expanded Details */}
          {expandedRun === run.id && (
            <div className="comic-panel p-3 mt-1 bg-muted/20">
              <div className="space-y-2">
                <p className="text-xs font-bold">Timeline:</p>
                {steps.map((step) => (
                  <div key={step.index} className="flex items-center gap-2 text-xs">
                    <span className="font-mono w-6">#{step.index}</span>
                    <span className="w-24">{step.worker}</span>
                    <span className="flex-1">
                      {step.status === 'done' && (
                        <span className="text-green-600">✓ Done</span>
                      )}
                      {step.status === 'running' && (
                        <span className="text-blue-600">⟳ Running...</span>
                      )}
                    </span>
                    {step.score !== null && (
                      <span className="font-mono">SCORE: {step.score.toFixed(2)}</span>
                    )}
                  </div>
                ))}
              </div>

              {/* Output Preview */}
              <div className="mt-3 pt-3 border-t-2 border-foreground">
                <p className="text-xs font-bold mb-1">Output Preview:</p>
                <div className="bg-background border border-foreground p-2 text-xs font-mono max-h-24 overflow-auto">
                  <p>✓ Task completed successfully</p>
                  <p>✓ Review passed with 0.95 score</p>
                  <p>→ Proceeding to next stage...</p>
                </div>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
