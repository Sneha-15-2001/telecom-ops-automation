import { Component, input } from '@angular/core';
import { Stage } from './bugzero-api.service';

/**
 * Renders the five-stage pipeline from slide 3 of the deck as it actually ran.
 *
 * A stage that found nothing reports honestly (warn/skip) rather than showing a
 * green tick — the point of this component is that an operator can trust the
 * green ones.
 */
@Component({
  standalone: true,
  selector: 'app-pipeline-timeline',
  template: `
    <ol class="pipeline">
      @for (stage of stages(); track stage.n) {
        <li class="stage" [attr.data-state]="stage.state">
          <span class="n">{{ stage.n }}</span>
          <div class="body">
            <h4>{{ stage.name }}</h4>
            <p class="detail">{{ stage.detail }}</p>
            @if (stage.count !== undefined && stage.count !== null) {
              <span class="count">{{ stage.count }} found</span>
            }
          </div>
          <span class="mark" [title]="stateLabel(stage.state)">{{ glyph(stage.state) }}</span>
        </li>
        @if (!$last) {
          <li class="link" aria-hidden="true"></li>
        }
      }
    </ol>
  `,
  styles: [`
    .pipeline { list-style: none; margin: 0; padding: 0; display: flex; align-items: stretch; gap: 0; flex-wrap: wrap; }
    .stage {
      flex: 1 1 150px; min-width: 150px; display: flex; align-items: flex-start; gap: .7rem;
      padding: .85rem .9rem; border: 1px solid var(--bz-line); border-radius: 10px;
      background: var(--bz-panel); position: relative;
    }
    .stage .n {
      flex: none; width: 22px; height: 22px; border-radius: 50%; font-size: .72rem; font-weight: 700;
      display: grid; place-items: center; background: var(--bz-accent-dim); color: var(--bz-accent);
    }
    .stage .body { min-width: 0; flex: 1; }
    .stage h4 { margin: 0 0 .2rem; font-size: .82rem; font-weight: 650; letter-spacing: .01em; }
    .stage .detail { margin: 0; font-size: .74rem; color: var(--bz-muted); overflow-wrap: anywhere; }
    .stage .count {
      display: inline-block; margin-top: .4rem; font-size: .66rem; padding: .1rem .4rem;
      border-radius: 999px; background: var(--bz-chip); color: var(--bz-muted);
    }
    .stage .mark { font-size: .95rem; line-height: 1; }
    .stage[data-state='ok']   .mark { color: var(--bz-ok); }
    .stage[data-state='warn'] .mark { color: var(--bz-warn); }
    .stage[data-state='skip'] { opacity: .55; }
    .stage[data-state='skip'] .mark { color: var(--bz-muted); }
    .stage[data-state='fail'] .mark { color: var(--bz-bad); }
    .link { flex: none; width: 18px; align-self: center; height: 2px; background: var(--bz-line); }
    @media (max-width: 720px) { .link { display: none; } .stage { flex-basis: 100%; } }
  `],
})
export class PipelineTimeline {
  readonly stages = input.required<Stage[]>();

  private readonly glyphs: Record<Stage['state'], string> = {
    ok: '✓', warn: '!', skip: '–', fail: '✕',
  };
  private readonly labels: Record<Stage['state'], string> = {
    ok: 'completed',
    warn: 'completed with nothing found — check configuration',
    skip: 'not available in this environment',
    fail: 'failed',
  };

  glyph(state: Stage['state']): string {
    return this.glyphs[state] ?? '·';
  }

  stateLabel(state: Stage['state']): string {
    return this.labels[state] ?? '';
  }
}