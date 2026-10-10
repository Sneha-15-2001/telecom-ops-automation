import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { BugZeroApi, HandoffResult, HealthReport, Investigation, QueuedIssue, RcaDoc, RcaRemediation } from './bugzero-api.service';
import { PipelineTimeline } from './pipeline-timeline.component';
import { HandoffPanel } from './handoff-panel.component';

/**
 * The BugZero console — slide 3's workflow, end to end.
 *
 * Intake is a real Jira ticket picker when Jira is connected, with free text as
 * the fallback rather than the primary path. The five-stage pipeline renders
 * from the backend's own `stages` array, so what the operator sees reflects
 * what actually ran.
 */
@Component({
  selector: 'app-investigator',
  imports: [FormsModule, PipelineTimeline, HandoffPanel],
  templateUrl: './investigator.component.html',
  styleUrl: './investigator.component.css',
})
export class Investigator {
  private api = inject(BugZeroApi);

  // --- intake -------------------------------------------------------------
  text = signal('');
  selectedIssue = signal<string | null>(null);
  jiraIssues = signal<QueuedIssue[]>([]);
  jiraConfigured = signal(false);
  issueSource = signal<'jira' | 'incident-queue'>('jira');
  issueFilter = signal('');
  issueKind = signal<'all' | 'http' | 'domain'>('all');
  httpFailureCount = signal(0);
  domainCount = signal(0);
  liveCount = signal(0);

  /** Tickets after the text filter and the All/HTTP/Domain tabs. */
  visibleIssues = computed(() => {
    const q = this.issueFilter().toLowerCase().trim();
    const kind = this.issueKind();
    return this.jiraIssues().filter((i) => {
      if (kind === 'http' && i.kind !== 'http-platform') return false;
      if (kind === 'domain' && i.kind === 'http-platform') return false;
      if (!q) return true;
      return [i.id, i.title, i.http_status, i.priority, i.error]
        .filter(Boolean)
        .some((f) => String(f).toLowerCase().includes(q));
    });
  });

  // --- state --------------------------------------------------------------
  busy = signal<'jira' | 'text' | null>(null);
  rca = signal<RcaDoc | null>(null);
  rcaBusy = signal(false);
  investigation = signal<Investigation | null>(null);
  health = signal<HealthReport | null>(null);
  section = signal<'evidence' | 'code' | 'rca'>('evidence');
  error = signal('');

  verdict = computed(() => this.investigation()?.verdict ?? null);
  stages = computed(() => this.investigation()?.stages ?? []);

  /** One chip per dependency, so an operator can see what is and isn't wired. */
  statusChips = computed(() => {
    const h = this.health();
    if (!h?.sources) return [];
    const s = h.sources;
    return [
      { label: `logs: ${s.logs?.mode ?? '?'}`, ok: !!s.logs?.mode && !s.logs?.error, detail: s.logs?.detail ?? s.logs?.error ?? '' },
      { label: `llm: ${s.llm?.engine ?? '?'}`, ok: !!s.llm?.configured, detail: s.llm?.detail ?? '' },
      { label: 'jira', ok: !!s.jira?.reachable, detail: s.jira?.detail ?? '' },
      { label: 'email', ok: !!s.email?.reachable, detail: s.email?.detail ?? '' },
      { label: 'history', ok: !!s.store?.reachable, detail: s.store?.detail ?? '' },
    ];
  });

  /** Code location resolved server-side during the handoff preview. */
  codeLocation = computed(() => {
    const m = this.handoffResult()?.code_fix?.location?.methods;
    return m?.[0] ?? null;
  });

  private handoffResult = signal<HandoffResult | null>(null);

  /** Flatten correlated log lines into the raw text an operator can read. */
  lineText(inv: Investigation): string {
    return (inv.log_lines ?? []).map((l) => `[${l.service}] ${l.line}`).join('\n');
  }

  constructor() {
    this.refreshHealth();
    this.loadIssues();
  }

  refreshHealth(): void {
    this.api.health().subscribe((h) => {
      if (h?.sources) {
        this.health.set(h);
        this.jiraConfigured.set(!!h.sources.jira?.configured);
      }
    });
  }

  loadIssues(): void {
    this.api.issues({ maxResults: 50 }).subscribe((r) => {
      if (r?.tickets?.length) {
        this.jiraIssues.set(r.tickets);
        this.issueSource.set(r.source === 'jira' ? 'jira' : 'incident-queue');
        this.httpFailureCount.set(r.http_failure_count ?? 0);
        this.domainCount.set((r.scenario_count ?? 0) - (r.http_failure_count ?? 0));
        this.liveCount.set(r.live_count ?? 0);
      } else if (r?.issues?.length) {
        // Real Jira shape, normalised to the same list.
        this.jiraIssues.set(r.issues.map((i: any) => ({ ...i, kind: 'domain' })));
        this.issueSource.set('jira');
      }
    });
  }

  /** "502 Bad Gateway" -> "502" */
  httpCode(status?: string | null): string {
    return (status ?? '').split(' ')[0] ?? '';
  }

  pickIssue(issue: QueuedIssue): void {
    this.selectedIssue.set(issue.id);
    this.busy.set('jira');
    this.error.set('');
    // Scenario and live cards carry their own ready-to-reason-over text; a real
    // Jira issue is fetched from Jira so the ticket is the source of truth.
    const run = issue.source === 'jira' && !issue.text
      ? this.api.investigateJira(issue.id)
      : this.api.investigate(issue.text || issue.title);
    run.subscribe((inv: Investigation) => {
      this.investigation.set(inv);
      inv.jira_source = { key: issue.id, summary: issue.title, http_status: issue.http_status };
      if (inv.llm?.enabled) {
        this.error.set('');
      } else if (inv.message) {
        this.error.set(inv.message);
      } else if (inv.llm?.reason) {
        this.error.set(inv.llm.reason);
      }
      this.busy.set(null);
      this.refreshHealth();
    });
  }

  runText(): void {
    const t = this.text().trim();
    if (!t) {
      this.error.set('Paste an incident description or pick a Jira ticket first.');
      return;
    }
    this.busy.set('text');
    this.error.set('');
    this.api.investigate(t).subscribe((inv) => this.apply(inv));
  }

  private apply(inv: Investigation | null): void {
    this.busy.set(null);
    if (!inv || (inv as any).error) {
      this.error.set((inv as any)?.error ?? 'Investigation returned nothing.');
      return;
    }
    this.investigation.set(inv);
    if (inv.llm && inv.llm.enabled) {
      this.error.set('');
    } else if (inv.message) {
      this.error.set(inv.message);
    } else if (inv.llm?.reason) {
      this.error.set(inv.llm.reason);
    }
    this.loadIssues();
  }

  onHandoffDone(result: HandoffResult | null): void {
    this.handoffResult.set(result);
    this.refreshHealth();
  }

  remediation = computed<RcaRemediation | null>(() => {
    return this.rca()?.remediation ?? this.handoffResult()?.remediation ?? null;
  });

  /** Build the RCA document, the proposed change and the action plan. */
  generateRca(): void {
    const inv = this.investigation();
    if (!inv) return;
    this.rcaBusy.set(true);
    this.rca.set(null);
    this.api.rca(inv.incident_text).subscribe((r) => {
      this.rcaBusy.set(false);
      if (r?.rca) {
        this.rca.set({
          ...r.rca,
          markdown: r.markdown ?? '',
          remediation: r.remediation ?? null,
        });
      }
    });
  }

  sourceIssueLabel(): string | null {
    return this.selectedIssue() ?? this.investigation()?.jira_source?.key ?? null;
  }
}