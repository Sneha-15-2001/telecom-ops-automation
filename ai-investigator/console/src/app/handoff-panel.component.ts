import { Component, computed, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { BugZeroApi, HandoffResult, Investigation } from './bugzero-api.service';

/**
 * Slide 3 stage 5 — "developer output".
 *
 * Two actions, deliberately separated:
 *   Preview   builds the ticket and email and shows them; transmits nothing.
 *   Hand off  creates the real Jira ticket and sends the real email.
 *
 * Preview is the default path because the handoff writes to systems other
 * people live in. An operator should always see the artifact first.
 */
@Component({
  standalone: true,
  selector: 'app-handoff-panel',
  imports: [FormsModule],
  template: `
    <section class="card">
      <header>
        <div>
          <h3>Developer handoff</h3>
          <p class="muted">Packages the evidence into a Jira ticket and emails it to the owning developer.</p>
        </div>
        @if (sourceIssue()) {
          <span class="chip">from {{ sourceIssue() }}</span>
        }
      </header>

      <div class="controls">
        <label class="field">
          <span>Recipients</span>
          <input type="text" [ngModel]="recipients()" (ngModelChange)="recipients.set($event)"
                 placeholder="Comma-separated emails (defaults to BUGZERO_MAIL_TO)" />
        </label>
        <div class="actions">
          <button class="btn ghost" (click)="preview()" [disabled]="busy()">
            {{ busy() === 'preview' ? 'Building…' : 'Preview handoff' }}
          </button>
          <button class="btn" (click)="send()" [disabled]="busy() !== null">
            {{ busy() === 'send' ? 'Handing off…' : 'Create ticket & email' }}
          </button>
        </div>
      </div>

      @if (result(); as h) {
        <div class="channels">
          <div class="chan" [class.ok]="h.jira.ok" [class.bad]="h.jira.attempted && !h.jira.ok">
            <strong>Jira</strong>
            @if (h.jira.ok) {
              <a [href]="h.jira.url" target="_blank" rel="noopener">{{ h.jira.key }}</a>
            } @else {
              <span>{{ h.jira.attempted ? (h.jira.error || 'failed') : 'not attempted' }}</span>
            }
          </div>
          <div class="chan" [class.ok]="h.email.sent" [class.bad]="h.email.attempted && !h.email.ok" [class.warn]="h.email.ok && !h.email.sent">
            <strong>Email</strong>
            @if (h.email.sent) {
              <span>sent to {{ h.email.to?.join(', ') }}</span>
            } @else if (h.email.ok) {
              <span>dry-run — {{ h.email.detail }}</span>
            } @else {
              <span>{{ h.email.attempted ? (h.email.error || 'failed') : 'not attempted' }}</span>
            }
          </div>
        </div>

        <div class="artifact">
          <div class="tabs">
            <button [class.active]="tab() === 'ticket'" (click)="tab.set('ticket')">Jira ticket</button>
            <button [class.active]="tab() === 'email'" (click)="tab.set('email')">Email</button>
            <button [class.active]="tab() === 'rca'" (click)="tab.set('rca')">RCA</button>
          </div>

          @switch (tab()) {
            @case ('ticket') {
              <div class="ticket-meta">
                <div><label>Summary</label><p class="strong">{{ h.issue_summary }}</p></div>
                <div class="row">
                  <div><label>Priority</label><p>{{ h.issue_priority }}</p></div>
                  <div><label>Labels</label><p class="labels">
                    @for (l of h.issue_labels; track l) { <span class="chip">{{ l }}</span> }
                  </p></div>
                  <div><label>Code location</label>
                    <p [class.ok-text]="h.code_location_verified" [class.warn-text]="!h.code_location_verified">
                      {{ h.code_location_verified ? 'verified in source' : 'not verified' }}
                    </p>
                  </div>
                </div>
              </div>
              <pre class="body">{{ h.issue_description }}</pre>
            }
            @case ('email') {
              <div class="ticket-meta"><div><label>Subject</label><p class="strong">{{ h.email_subject }}</p></div></div>
              <pre class="body">{{ h.email_body }}</pre>
            }
            @case ('rca') {
              <pre class="body">{{ h.rca_markdown || 'Run a preview to generate the RCA.' }}</pre>
            }
          }
        </div>

        <div class="foot">
          <span class="muted">{{ h.safety }}</span>
          @if (h.investigation_ref) {
            <span class="muted">Saved as {{ h.investigation_ref }}</span>
          }
        </div>
      }
    </section>
  `,
  styles: [`
    .controls { display: flex; gap: .8rem; align-items: flex-end; flex-wrap: wrap; margin: .8rem 0; }
    .field { flex: 1 1 260px; display: flex; flex-direction: column; gap: .25rem; }
    .field span { font-size: .72rem; color: var(--bz-muted); }
    .field input { padding: .45rem .6rem; }
    .actions { display: flex; gap: .5rem; }
    .channels { display: flex; gap: .6rem; flex-wrap: wrap; margin-bottom: .8rem; }
    .chan {
      flex: 1 1 220px; padding: .55rem .7rem; border-radius: 8px; font-size: .78rem;
      border: 1px solid var(--bz-line); background: var(--bz-chip); display: flex; gap: .5rem; align-items: baseline;
    }
    .chan.ok { border-color: var(--bz-ok); }
    .chan.bad { border-color: var(--bz-bad); }
    .chan.warn { border-color: var(--bz-warn); }
    .ticket-meta { padding: .7rem .85rem; background: var(--bz-chip); border-radius: 8px; margin-bottom: .6rem; }
    .ticket-meta label { font-size: .68rem; text-transform: uppercase; letter-spacing: .06em; color: var(--bz-muted); }
    .ticket-meta p { margin: .15rem 0 0; font-size: .82rem; }
    .ticket-meta .row { display: flex; gap: 1.4rem; flex-wrap: wrap; margin-top: .5rem; }
    .labels { display: flex; gap: .3rem; flex-wrap: wrap; }
    .body { max-height: 420px; overflow: auto; font-size: .74rem; }
  `],
})
export class HandoffPanel {
  private api = inject(BugZeroApi);

  readonly incidentText = input.required<string>();
  readonly sourceIssue = input<string | null>(null);
  readonly investigation = input<Investigation | null>(null);
  readonly completed = output<HandoffResult | null>();

  result = signal<HandoffResult | null>(null);
  busy = signal<'preview' | 'send' | null>(null);
  tab = signal<'ticket' | 'email' | 'rca'>('ticket');
  recipients = signal('');

  toAddrs(): string[] | undefined {
    return this.recipients().split(',').map((s) => s.trim()).filter(Boolean);
  }

  preview(): void {
    this.busy.set('preview');
    this.api.handoffPreview(this.incidentText(), this.sourceIssue()).subscribe({
      next: (r) => {
        this.result.set(r);
        this.busy.set(null);
        this.completed.emit(r);
      },
      error: () => this.busy.set(null),
    });
  }

  send(): void {
    this.busy.set('send');
    this.api.handoff(this.incidentText(), this.sourceIssue(), {
      createTicket: true,
      sendEmail: true,
      to: this.toAddrs(),
    }).subscribe({
      next: (r) => {
        this.result.set(r);
        this.busy.set(null);
        this.completed.emit(r);
      },
      error: () => this.busy.set(null),
    });
  }
}