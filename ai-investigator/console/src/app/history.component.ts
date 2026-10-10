import { Component, inject, signal } from '@angular/core';
import { DatePipe, JsonPipe } from '@angular/common';
import { BugZeroApi } from './bugzero-api.service';

/**
 * Investigation history — the "workflow + history" box on slide 4.
 *
 * Reads BugZero's own database, never the five service databases, so this is
 * the one screen that can be written to safely.
 */
@Component({
  selector: 'app-history',
  imports: [DatePipe, JsonPipe],
  template: `
    <div class="page">
      <header class="intro">
        <h1>History</h1>
        <p class="muted">Every investigation BugZero ran, what it concluded, and what was handed to a developer.</p>
      </header>

      @if (stats(); as s) {
        <div class="stats">
          <div class="tile"><strong>{{ s.investigations ?? 0 }}</strong><span>investigations</span></div>
          <div class="tile"><strong>{{ s.completed ?? 0 }}</strong><span>completed</span></div>
          <div class="tile"><strong>{{ s.high_confidence ?? 0 }}</strong><span>high confidence</span></div>
          <div class="tile"><strong>{{ s.tickets_created ?? 0 }}</strong><span>tickets created</span></div>
          <div class="tile"><strong>{{ s.emails_sent ?? 0 }}</strong><span>emails sent</span></div>
        </div>
      }

      @if (!loading() && !rows().length) {
        <section class="card">
          <p class="muted">
            No investigations recorded yet.
            @if (storeError()) { <span>{{ storeError() }}</span> }
          </p>
          <button class="btn ghost" (click)="load()">Retry</button>
        </section>
      }

      <section class="card">
        <table class="grid">
          <thead>
            <tr><th>Ref</th><th>Source</th><th>Verdict</th><th>Type</th><th>Conf.</th><th>Jira</th><th>When</th></tr>
          </thead>
          <tbody>
            @for (r of rows(); track r.id) {
              <tr (click)="open(r)" class="clickable">
                <td class="mono">{{ r.reference }}</td>
                <td class="mono">{{ r.source_issue || '—' }}</td>
                <td class="verdict-cell">{{ r.verdict_summary || '—' }}</td>
                <td><span class="badge sm">{{ r.fix_type || '—' }}</span></td>
                <td>{{ r.confidence || '—' }}</td>
                <td class="mono">
                  @if (r.jira_key) { <span class="ok-text">{{ r.jira_key }}</span> }
                  @else if (r.email_sent) { <span class="ok-text">email</span> }
                  @else { — }
                </td>
                <td class="muted small">{{ r.created_at | date: 'MMM d, HH:mm' }}</td>
              </tr>
            }
          </tbody>
        </table>
      </section>

      @if (detail(); as d) {
        <section class="card">
          <h3>{{ d.reference }} <span class="muted small">{{ d.created_at | date: 'medium' }}</span></h3>
          <p><strong>Incident:</strong> {{ d.incident_text }}</p>
          @if (d.verdict_summary) { <p><strong>Verdict:</strong> {{ d.verdict_summary }}</p> }
          @if (d.verdict_root) { <p><strong>Root cause:</strong> {{ d.verdict_root }}</p> }
          @if (d.jira_key) { <p><strong>Jira:</strong> <span class="mono">{{ d.jira_key }}</span></p> }
          @if (d.error) { <p class="warn-text">{{ d.error }}</p> }
          @if (d.evidence_pack) {
            <h4 class="small">Evidence pack</h4>
            <pre class="log">{{ d.evidence_pack | json }}</pre>
          }
        </section>
      }
    </div>
  `,
  styles: [`
    .stats { display: flex; gap: .6rem; flex-wrap: wrap; margin-bottom: 1rem; }
    .tile {
      flex: 1 1 130px; padding: .7rem .8rem; border-radius: 10px;
      border: 1px solid var(--bz-line); background: var(--bz-panel); display: flex; flex-direction: column;
    }
    .tile strong { font-size: 1.4rem; line-height: 1.1; }
    .tile span { font-size: .72rem; color: var(--bz-muted); }
    .verdict-cell { max-width: 320px; }
  `],
})
export class History {
  private api = inject(BugZeroApi);

  rows = signal<any[]>([]);
  stats = signal<any>(null);
  detail = signal<any>(null);
  loading = signal(true);
  storeError = signal('');

  constructor() {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.api.history(50).subscribe((r) => {
      this.loading.set(false);
      if (r?.ok === false) {
        this.storeError.set(r.error ?? 'History store unavailable.');
        return;
      }
      this.storeError.set('');
      this.rows.set(r?.investigations ?? []);
      this.stats.set(r?.stats ?? null);
    });
  }

  open(row: any): void {
    this.api.historyDetail(row.reference).subscribe((r) => {
      if (r?.ok) this.detail.set(r.investigation);
    });
  }
}