/** Maps a job status string to the correct CSS badge class and display label. */
const STATUS_MAP = {
  QUEUED:     { cls: 'badge-queued',     label: 'Queued' },
  PROCESSING: { cls: 'badge-processing', label: 'Processing' },
  COMPLETED:  { cls: 'badge-completed',  label: 'Completed' },
  CACHED:     { cls: 'badge-cached',     label: 'Cached' },
  FAILED:     { cls: 'badge-failed',     label: 'Failed' },
};

export default function StatusBadge({ status }) {
  const { cls, label } = STATUS_MAP[status] ?? { cls: '', label: status };
  return <span className={`badge ${cls}`}>{label}</span>;
}
