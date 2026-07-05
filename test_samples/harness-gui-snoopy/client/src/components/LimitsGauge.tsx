import React from 'react';
import { AlertCircle, CheckCircle, AlertTriangle } from 'lucide-react';

interface LimitsGaugeProps {
  limits: {
    [key: string]: {
      used_5h: number;
      used_7d: number;
      plan: string;
      status: 'ok' | 'warn' | 'stop';
    };
  };
}

export default function LimitsGauge({ limits }: LimitsGaugeProps) {
  const tools = ['claude', 'codex', 'gemini'];

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'ok':
        return 'border-green-600 bg-green-50';
      case 'warn':
        return 'border-yellow-600 bg-yellow-50';
      case 'stop':
        return 'border-red-600 bg-red-50';
      default:
        return 'border-foreground';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'ok':
        return <CheckCircle size={16} className="text-green-600" />;
      case 'warn':
        return <AlertTriangle size={16} className="text-yellow-600" />;
      case 'stop':
        return <AlertCircle size={16} className="text-red-600" />;
      default:
        return null;
    }
  };

  const getProgressColor = (percentage: number) => {
    if (percentage < 80) return 'bg-green-600';
    if (percentage < 100) return 'bg-yellow-600';
    return 'bg-red-600';
  };

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold">Tool Limits</h2>
      
      {tools.map((tool) => {
        const limit = limits[tool];
        const maxUsage = Math.max(limit.used_5h, limit.used_7d);
        
        return (
          <div
            key={tool}
            className={`comic-panel p-3 ${getStatusColor(limit.status)}`}
          >
            <div className="flex items-start justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className="font-bold text-sm uppercase">{tool}</span>
                {getStatusIcon(limit.status)}
              </div>
              <span className="text-xs font-mono bg-background px-2 py-1">
                {limit.plan}
              </span>
            </div>

            {/* 5-hour gauge */}
            <div className="mb-2">
              <div className="flex justify-between items-center mb-1">
                <span className="text-xs font-bold">5h</span>
                <span className="text-xs font-mono">{limit.used_5h}%</span>
              </div>
              <div className="w-full h-3 border-2 border-foreground bg-background">
                <div
                  className={`h-full ${getProgressColor(limit.used_5h)}`}
                  style={{ width: `${Math.min(limit.used_5h, 100)}%` }}
                />
              </div>
            </div>

            {/* 7-day gauge */}
            <div>
              <div className="flex justify-between items-center mb-1">
                <span className="text-xs font-bold">7d</span>
                <span className="text-xs font-mono">{limit.used_7d}%</span>
              </div>
              <div className="w-full h-3 border-2 border-foreground bg-background">
                <div
                  className={`h-full ${getProgressColor(limit.used_7d)}`}
                  style={{ width: `${Math.min(limit.used_7d, 100)}%` }}
                />
              </div>
            </div>

            {/* Stats */}
            <div className="mt-2 pt-2 border-t-2 border-foreground text-xs space-y-1">
              <div className="flex justify-between">
                <span>Calls:</span>
                <span className="font-mono">1,234</span>
              </div>
              <div className="flex justify-between">
                <span>Tokens:</span>
                <span className="font-mono">45.2K</span>
              </div>
              <div className="flex justify-between">
                <span>Cost:</span>
                <span className="font-mono">$2.34</span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
