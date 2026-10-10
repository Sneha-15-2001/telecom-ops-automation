/**
 * BugZero backend API surface (:8090).
 *
 * Every method returns an Observable and never throws for an expected failure —
 * the backend encodes errors in the payload (ok/error), so components branch on
 * the response rather than catching. The one exception is a transport failure,
 * which surfaces as an HttpErrorResponse and is handled by the interceptor
 * below into a readable message.
 */
import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, of } from 'rxjs';

/**
 * Dev goes through the Angular proxy (proxy.conf.json -> :8090) so there is no
 * CORS origin to keep in sync. Set the build-time API_BASE to an absolute URL
 * when serving the console from somewhere other than its own dev server.
 */
export const API_BASE = '';

/** One step of the slide-3 pipeline, rendered by the timeline component. */
export interface Stage {
  n: number;
  name: string;
  detail: string;
  state: 'ok' | 'warn' | 'skip' | 'fail';
  count?: number;
}

/**
 * Unified ticket shape. Jira issues and built-in scenario cards are normalised
 * into this by the backend so the console has one list to render.
 */
export interface QueuedIssue {
  id: string;
  title: string;
  status?: string;
  priority?: string;
  reporter?: string;
  source?: 'jira' | 'scenario' | 'live';
  /** http-platform covers 501/502/503/429/500/409/415 — the platform-failure set. */
  kind?: 'http-platform' | 'domain';
  http_status?: string | null;
  requires_chaos?: boolean;
  summary?: string;
  text?: string;
  error?: string;
}

export interface Investigation {
  incident_text: string;
  entities: Record<string, string[]>;
  verdict?: { summary: string; root_cause: string; fix_type: string; confidence: string } | null;
  llm?: { enabled: boolean; engine?: string; model?: string; analysis?: any; reason?: string };
  journey?: Array<{ service: string; event: string; status: string; time: string }>;
  log_lines?: Array<{ service: string; line: string }>;
  log_stats?: { direct_hits: number; lines: number };
  correlations?: string[];
  db_evidence?: Record<string, any[]>;
  owner?: { services: string[] };
  has_error?: boolean;
  needs_info?: boolean;
  message?: string;
  note?: string;
  safety?: string;
  stages?: Stage[];
  candidates?: any[];
  jira_source?: any;
  investigation_ref?: string;
  persisted?: boolean;
  warnings?: string[];
}

export interface CodeLocation {
  class_file?: { file: string; lines: number } | null;
  methods?: Array<{ file: string; name: string; start_line: number; end_line: number; source: string }>;
}

export interface HandoffResult {
  source_issue?: string;
  issue_summary: string;
  issue_description: string;
  issue_labels: string[];
  issue_priority: string;
  email_subject: string;
  email_body: string;
  evidence_pack: any;
  code_fix?: { location?: CodeLocation; verified?: boolean; service?: string; class_name?: string; method?: string; before?: string; after?: string };
  code_location_verified: boolean;
  jira: { attempted: boolean; ok: boolean; key?: string; url?: string; error?: string; link?: any };
  email: { attempted: boolean; ok: boolean; sent?: boolean; to?: string[]; error?: string; detail?: string; mode?: string };
  investigation_ref?: string;
  handoff_ref?: string;
  persisted?: boolean;
  dry_run?: boolean;
  note?: string;
  rca_markdown?: string;
  safety: string;
}

export interface JiraIssue {
  key: string;
  summary: string;
  status: string;
  priority: string;
  reporter: string;
  created: string;
  labels: string[];
}

export interface HealthReport {
  status: string;
  service: string;
  sources: {
    logs: any;
    ssh: any;
    llm: any;
    jira: any;
    email: any;
    store: any;
  };
}

@Injectable({ providedIn: 'root' })
export class BugZeroApi {
  private http = inject(HttpClient);

  /** Every call degrades to a payload the UI can render, never a thrown error. */
  private call<T>(obs: Observable<T>): Observable<T> {
    return obs.pipe(
      catchError((err: HttpErrorResponse) => {
        const detail = err.error?.detail || err.error?.message || err.message || 'unknown error';
        return of({
          ok: false,
          error: `Investigator backend unreachable (${detail}). Start it on :8090.`,
        } as unknown as T);
      }),
    );
  }

  health(): Observable<HealthReport> {
    return this.call(this.http.get<HealthReport>(`${API_BASE}/api/health`));
  }

  investigate(text: string, mode = 'llm'): Observable<Investigation> {
    return this.call(this.http.post<Investigation>(`${API_BASE}/api/investigate`, { incident_text: text, mode }));
  }

  /** Slide 3 stage 1 — pull the real ticket, then investigate it. */
  investigateJira(issueKey: string, mode = 'llm'): Observable<Investigation> {
    return this.call(this.http.post<Investigation>(`${API_BASE}/api/investigate/jira`, { issue_key: issueKey, mode }));
  }

  /**
   * Ticket queue for the picker. Returns Jira issues when Jira is configured,
   * otherwise the built-in incident queue (scenario cards + live DB findings).
   */
  issues(params: { projectKey?: string; jql?: string; maxResults?: number } = {}): Observable<any> {
    let httpParams = new HttpParams();
    if (params.projectKey) httpParams = httpParams.set('project_key', params.projectKey);
    if (params.jql) httpParams = httpParams.set('jql', params.jql);
    if (params.maxResults) httpParams = httpParams.set('max_results', params.maxResults);
    return this.call(this.http.get<any>(`${API_BASE}/api/jira/issues`, { params: httpParams }));
  }

  jiraIssue(key: string): Observable<any> {
    return this.call(this.http.get<any>(`${API_BASE}/api/jira/issue`, { params: { issue_key: key } }));
  }

  rca(text: string, mode = 'llm'): Observable<any> {
    return this.call(this.http.post<any>(`${API_BASE}/api/rca`, { incident_text: text, mode }));
  }

  dataFix(text: string, mode = 'llm'): Observable<any> {
    return this.call(this.http.post<any>(`${API_BASE}/api/datafix`, { incident_text: text, mode }));
  }

  codeFix(text: string, mode = 'llm'): Observable<any> {
    return this.call(this.http.post<any>(`${API_BASE}/api/codefix`, { incident_text: text, mode }));
  }

  /** Slide 3 stage 5 — build the ticket + email and transmit them. */
  handoff(text: string, sourceIssueKey: string | null, opts: { createTicket: boolean; sendEmail: boolean; to?: string[] }): Observable<HandoffResult> {
    return this.call(this.http.post<HandoffResult>(`${API_BASE}/api/handoff`, {
      incident_text: text,
      source_issue_key: sourceIssueKey,
      create_ticket: opts.createTicket,
      send_email: opts.sendEmail,
      to_addrs: opts.to ?? null,
    }));
  }

  /** Same artifacts, nothing transmitted — what the Preview button calls. */
  handoffPreview(text: string, sourceIssueKey: string | null): Observable<HandoffResult> {
    return this.call(this.http.post<HandoffResult>(`${API_BASE}/api/handoff/dry-run`, {
      incident_text: text,
      source_issue_key: sourceIssueKey,
    }));
  }

  commentOnIssue(issueKey: string, text: string): Observable<any> {
    return this.call(this.http.post<any>(`${API_BASE}/api/handoff/comment`, { issue_key: issueKey, incident_text: text }));
  }

  incidentQueue(): Observable<any> {
    return this.call(this.http.get<any>(`${API_BASE}/api/incidents`));
  }

  history(limit = 50): Observable<any> {
    return this.call(this.http.get<any>(`${API_BASE}/api/history`, { params: { limit } }));
  }

  historyDetail(reference: string): Observable<any> {
    return this.call(this.http.get<any>(`${API_BASE}/api/history/detail`, { params: { reference } }));
  }

  setupSchema(): Observable<any> {
    return this.call(this.http.post<any>(`${API_BASE}/api/setup/schema`, {}));
  }
}