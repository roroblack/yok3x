import React, { useState } from 'react';
import { ChevronLeft, Search, Save, Upload, AlertTriangle } from 'lucide-react';

interface KnotViewerProps {
  onBack?: () => void;
}

export default function KnotViewer({ onBack }: KnotViewerProps = {}) {
  const [selectedNote, setSelectedNote] = useState('architecture');
  const [searchQuery, setSearchQuery] = useState('');
  const [notes, setNotes] = useState([
    {
      id: 'architecture',
      title: 'System Architecture',
      tags: ['system', 'design'],
      links: ['[[orchestrator]]', '[[guard]]'],
      content: `# System Architecture

## Overview
The Harness orchestrator coordinates multiple LLM agents.

## Components
- **Producer**: Generates initial response
- **Reviewer**: Evaluates and provides feedback
- **Guard**: Enforces usage limits

## Data Flow
Producer → Reviewer → Guard Check → Output`,
    },
    {
      id: 'orchestrator',
      title: 'Orchestrator Patterns',
      tags: ['patterns', 'workflow'],
      links: ['[[architecture]]', '[[guard]]'],
      content: `# Orchestrator Patterns

## Producer-Reviewer
- Producer generates output
- Reviewer provides score
- Loop until pass_score met

## Pipeline
- Sequential stages
- Each stage processes output of previous

## Fanout-Fanin
- Parallel execution
- Join results at end`,
    },
    {
      id: 'guard',
      title: 'Guard System',
      tags: ['limits', 'safety'],
      links: ['[[architecture]]'],
      content: `# Guard System

## Purpose
Enforce usage limits and prevent overspending.

## Thresholds
- **Soft Limit**: 80% - Warning
- **Hard Limit**: 100% - Block

## Behavior
- Soft: Emit warning, continue
- Hard: Block call, stop loop`,
    },
  ]);

  const currentNote = notes.find((n) => n.id === selectedNote);
  const filteredNotes = notes.filter(
    (n) =>
      n.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      n.tags.some((t) => t.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  const getLintIssues = (): string[] => {
    const issues: string[] = [];
    if (!currentNote) return issues;

    // Check for broken links
    const linkPattern = /\[\[(\w+)\]\]/g;
    let match;
    while ((match = linkPattern.exec(currentNote.content)) !== null) {
      const linkedId = match[1].toLowerCase();
      if (!notes.some((n) => n.id === linkedId)) {
        issues.push(`Broken link: [[${match[1]}]]`);
      }
    }

    // Check for missing frontmatter
    if (!currentNote.tags || currentNote.tags.length === 0) {
      issues.push('Missing tags in frontmatter');
    }

    return issues;
  };

  const lintIssues = getLintIssues();

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
            <h1 className="text-2xl font-bold">Knowledge Graph (Knot)</h1>
            <p className="text-sm text-muted-foreground">Browse and edit interconnected notes</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Left: Note List */}
        <div className="lg:col-span-1">
          <div className="comic-panel p-4 space-y-3">
            {/* Search */}
            <div className="relative">
              <Search size={16} className="absolute left-2 top-2.5 text-muted-foreground" />
              <input
                type="text"
                placeholder="Search notes..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-8 pr-2 py-1 border-2 border-foreground bg-background text-sm"
              />
            </div>

            {/* Note List */}
            <div className="space-y-1 max-h-96 overflow-auto">
              {filteredNotes.map((note) => (
                <button
                  key={note.id}
                  onClick={() => setSelectedNote(note.id)}
                  className={`w-full text-left px-2 py-2 text-xs font-bold transition-colors ${
                    selectedNote === note.id
                      ? 'bg-accent text-accent-foreground'
                      : 'hover:bg-muted'
                  }`}
                >
                  <div>{note.title}</div>
                  <div className="text-xs font-normal text-muted-foreground mt-0.5">
                    {note.tags.join(', ')}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Right: Note Content */}
        <div className="lg:col-span-3 space-y-4">
          {currentNote && (
            <>
              {/* Note Header */}
              <div className="comic-panel p-4">
                <h2 className="text-2xl font-bold mb-2">{currentNote.title}</h2>
                <div className="flex flex-wrap gap-2">
                  {currentNote.tags.map((tag) => (
                    <span
                      key={tag}
                      className="text-xs bg-muted text-muted-foreground px-2 py-1 border border-foreground"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>

              {/* Note Content */}
              <div className="comic-panel p-4">
                <div className="prose prose-sm max-w-none text-sm font-mono whitespace-pre-wrap">
                  {currentNote.content}
                </div>
              </div>

              {/* Links */}
              {currentNote.links.length > 0 && (
                <div className="comic-panel p-4">
                  <p className="text-sm font-bold mb-2">Related Notes</p>
                  <div className="flex flex-wrap gap-2">
                    {currentNote.links.map((link) => {
                      const linkId = link.replace(/\[\[|\]\]/g, '').toLowerCase();
                      return (
                        <button
                          key={link}
                          onClick={() => setSelectedNote(linkId)}
                          className="text-xs bg-secondary text-secondary-foreground px-2 py-1 border-2 border-foreground hover:bg-accent transition-colors"
                        >
                          {link}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Lint Results */}
              {lintIssues.length > 0 && (
                <div className="comic-panel p-4 border-yellow-600 bg-yellow-50">
                  <div className="flex items-start gap-2">
                    <AlertTriangle size={16} className="text-yellow-600 flex-shrink-0 mt-0.5" />
                    <div className="text-sm">
                      <p className="font-bold text-yellow-600">Lint Issues</p>
                      <ul className="mt-1 text-xs text-muted-foreground space-y-1">
                        {lintIssues.map((issue, i) => (
                          <li key={i}>• {issue}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </div>
              )}

              {/* Actions */}
              <div className="flex gap-2">
                <button className="flex-1 comic-panel px-4 py-2 bg-accent text-accent-foreground font-bold text-sm hover:opacity-90 transition-opacity flex items-center justify-center gap-2">
                  <Save size={16} />
                  Save
                </button>
                <button className="flex-1 comic-panel px-4 py-2 font-bold text-sm hover:bg-muted transition-colors flex items-center justify-center gap-2">
                  <Upload size={16} />
                  Ingest
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
