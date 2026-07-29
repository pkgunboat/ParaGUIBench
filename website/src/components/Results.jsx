import React from "react";

import { Icon } from "./Icon.jsx";

/**
 * 渲染论文稿件结果与公开包验证台账，防止把两种证据范围混为一谈。
 *
 * @param {{copy: object, dataset: object | null}} props - 本地化文案和公开 manifest 派生数据。
 * @returns {React.ReactElement} 结果与验证状态段落。
 */
export function Results({ copy, dataset }) {
  const summary = dataset?.summary;
  const taskCount = summary?.task_count ?? "—";
  const liveCount = summary?.support_status_counts?.live_validated ?? "—";
  const pendingCount = summary?.support_status_counts?.blocked ?? "—";

  return (
    <section className="section section-results" id="results">
      <div className="site-shell">
        <div className="section-heading results-heading">
          <div>
            <h2>{copy.title}</h2>
          </div>
          <div className="section-prose">
            <p className="lead">{copy.intro}</p>
          </div>
        </div>

        <div className="results-grid">
          <article className="paper-results">
            <div className="article-heading">
              <span>{copy.paperTitle}</span>
            </div>
            <div className="paper-metrics">
              <div>
                <strong>{copy.success}</strong>
                <span>{copy.successLabel}</span>
              </div>
              <div>
                <strong>{copy.gain}</strong>
                <span>{copy.gainLabel}</span>
              </div>
            </div>
            <div className="result-bars" aria-hidden="true">
              <div>
                <span style={{ width: "46.4%" }} />
              </div>
              <div>
                <span style={{ width: "33.5%" }} />
              </div>
              <div>
                <span style={{ width: "27%" }} />
              </div>
            </div>
            <p className="evidence-note">
              <Icon name="warning" size={17} />
              {copy.paperNote}
            </p>
          </article>

          <article className="validation-ledger">
            <div className="article-heading">
              <span>{copy.ledgerTitle}</span>
            </div>
            <div className="ledger-counters">
              <div>
                <strong>{taskCount}</strong>
                <span>{copy.definitions}</span>
              </div>
              <div className="ledger-live">
                <strong>{liveCount}</strong>
                <span>{copy.live}</span>
              </div>
              <div className="ledger-pending">
                <strong>{pendingCount}</strong>
                <span>{copy.pending}</span>
              </div>
            </div>
            <div className="validation-checks">
              {copy.checks.map(([name, scope], index) => (
                <div key={name}>
                  <Icon name={index === 0 ? "database" : "check"} size={18} />
                  <strong>{name}</strong>
                  <span>
                    <small>{copy.scope}</small>
                    {scope}
                  </span>
                </div>
              ))}
            </div>
          </article>
        </div>
      </div>
    </section>
  );
}
