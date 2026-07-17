import { ArrowLeft, ExternalLink } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useAuditLogDetail } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "full",
    timeStyle: "medium",
  }).format(new Date(value));
}

export function EventDetailPage() {
  const { id, source } = useParams();
  const navigate = useNavigate();
  const event = useAuditLogDetail(id);

  return (
    <>
      <PageHeader
        title="Event Detail"
        description="Read-only audit event record with structured operational references."
        action={
          <button className="dashboard-metric-retry" onClick={() => navigate(-1)} type="button">
            <ArrowLeft size={15} />
            Back
          </button>
        }
      />

      <DataState isLoading={event.isLoading} isError={event.isError} error={event.error}>
        {event.data && (
          <div className="event-detail-layout">
            <section className="event-detail-card">
              <div className="event-detail-heading">
                <span className="event-source-pill">{source || event.data.source}</span>
                <strong>{event.data.event_type_label}</strong>
                <small>{event.data.event_category}</small>
              </div>
              <p>{event.data.message}</p>
              <dl className="event-detail-grid">
                <div>
                  <dt>Timestamp</dt>
                  <dd>{formatDateTime(event.data.created_at)}</dd>
                </div>
                <div>
                  <dt>Actor</dt>
                  <dd>{event.data.actor_display || event.data.actor_username || "System"}</dd>
                </div>
                <div>
                  <dt>Branch</dt>
                  <dd>{event.data.branch_code || "Not recorded"}</dd>
                </div>
                <div>
                  <dt>Related object</dt>
                  <dd>{event.data.entity_name}{event.data.entity_id ? ` / ${event.data.entity_id}` : ""}</dd>
                </div>
              </dl>
            </section>

            <section className="event-detail-card">
              <div className="section-header">
                <h2>Structured Metadata</h2>
              </div>
              {event.data.metadata.length === 0 ? (
                <p className="empty-panel-text">No structured metadata was recorded for this event.</p>
              ) : (
                <dl className="event-detail-grid">
                  {event.data.metadata.map((item) => (
                    <div key={`${item.label}-${item.value}`}>
                      <dt>{item.label}</dt>
                      <dd>{item.value}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </section>

            <section className="event-detail-card">
              <div className="section-header">
                <h2>Related Records</h2>
              </div>
              {event.data.related_links.length === 0 ? (
                <p className="empty-panel-text">No safe structured related links are available.</p>
              ) : (
                <div className="event-related-links">
                  {event.data.related_links.map((link) => (
                    <Link key={`${link.label}-${link.url}`} to={link.url}>
                      {link.label}
                      <ExternalLink size={14} />
                    </Link>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
      </DataState>
    </>
  );
}
