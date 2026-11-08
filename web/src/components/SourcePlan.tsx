import type { ContinuationPlan } from "../api/sourceBooks";

export default function SourcePlan({ plan }: { plan: ContinuationPlan }) {
  return (
    <section className="source-plan" aria-label="续写规划">
      <header>
        <span className="source-plan-label">续写提案 · 待讨论</span>
        <h3>{plan.premise}</h3>
        <p>{plan.overall_arc}</p>
      </header>
      <div className="source-plan-cards">
        {plan.cards.map((card, i) => (
          <article key={i}>
            <div className="source-plan-card-title">
              <span>{String(i + 1).padStart(2, "0")}</span>
              <h4>{card.title}</h4>
            </div>
            <p>
              <strong>叙事视角</strong> {card.pov}
            </p>
            <strong>核心剧情</strong>
            <ol>
              {card.core_plot.map((event, n) => (
                <li key={n}>{event}</li>
              ))}
            </ol>
            <p>
              <strong>看点 / 爽点</strong> {card.reader_payoff}
            </p>
            <p>
              <strong>伏笔与兑现</strong> {card.foreshadowing}
            </p>
            <p className="source-plan-hook">
              <strong>章末钩子</strong> {card.ending_hook}
            </p>
            <details>
              <summary>原作承接点</summary>
              <p>{card.source_anchor}</p>
            </details>
          </article>
        ))}
      </div>
      <details>
        <summary>建议结局与待选走向</summary>
        <p>{plan.proposed_ending}</p>
        <ul>
          {plan.major_choices.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      </details>
      {!!plan.source_conflicts.length && (
        <details>
          <summary>需要协调的设定</summary>
          <ul>
            {plan.source_conflicts.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
