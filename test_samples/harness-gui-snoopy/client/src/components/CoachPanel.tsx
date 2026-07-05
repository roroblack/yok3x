import React from 'react';
import { AlertTriangle, AlertCircle, CheckCircle } from 'lucide-react';

interface CoachPanelProps {
  limits: {
    [key: string]: {
      used_5h: number;
      used_7d: number;
      plan: string;
      status: 'ok' | 'warn' | 'stop';
    };
  };
  guardEnabled: boolean;
}

export default function CoachPanel({ limits, guardEnabled }: CoachPanelProps) {
  const getCoachMessage = (tool: string, status: string, used_5h: number, used_7d: number) => {
    if (status === 'stop') {
      return {
        icon: AlertCircle,
        title: 'Blocked!',
        message: `${tool} is at limit. Switch to another tool or wait for reset.`,
        color: 'text-red-600',
      };
    }
    if (status === 'warn') {
      const resetIn = Math.floor(Math.random() * 4) + 1;
      return {
        icon: AlertTriangle,
        title: 'Running Low',
        message: `${tool} at ${Math.max(used_5h, used_7d)}%. Resets in ${resetIn}h.`,
        color: 'text-yellow-600',
      };
    }
    return null;
  };

  const warnings = Object.entries(limits)
    .map(([tool, data]) => ({
      tool,
      ...getCoachMessage(tool, data.status, data.used_5h, data.used_7d),
    }))
    .filter((w) => w.message);

  return (
    <div className="comic-panel p-4">
      <h2 className="text-lg font-bold mb-3">Coach</h2>
      
      {!guardEnabled && (
        <div className="speech-bubble mb-3 bg-yellow-50 border-yellow-600">
          <p className="text-xs">
            ⚠️ Guard is OFF. Limits won't block calls!
          </p>
        </div>
      )}

      {warnings.length === 0 ? (
        <div className="speech-bubble bg-green-50 border-green-600">
          <div className="flex items-center gap-2">
            <CheckCircle size={16} className="text-green-600 flex-shrink-0" />
            <p className="text-xs">All tools running smoothly!</p>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {warnings.map((warning) => (
            <div key={warning.tool} className="speech-bubble">
              <div className="flex items-start gap-2">
                {warning.icon && (
                  <warning.icon size={14} className={`${warning.color} flex-shrink-0 mt-0.5`} />
                )}
                <div className="text-xs">
                  <p className="font-bold">{warning.title}</p>
                  <p className="text-muted-foreground">{warning.message}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Recommendations */}
      <div className="mt-3 pt-3 border-t-2 border-foreground">
        <p className="text-xs font-bold mb-2">💡 Suggestions:</p>
        <ul className="text-xs space-y-1 text-muted-foreground">
          <li>• Use Codex for simple tasks (cheaper)</li>
          <li>• Save Claude for complex reviews</li>
          <li>• Monitor daily budget closely</li>
        </ul>
      </div>
    </div>
  );
}
