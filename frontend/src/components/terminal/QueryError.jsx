export const QueryError = ({ onRetry }) => (
  <div role="alert" className="panel p-5 my-3 text-sm text-slate-300">
    <p>Data could not be refreshed. Any previous figures may be out of date.</p>
    {onRetry && <button type="button" className="mt-2 text-brand underline" onClick={onRetry}>Try again</button>}
  </div>
);
