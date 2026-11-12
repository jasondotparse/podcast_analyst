// Shared Previous/Next controls and progress dots for the app's short walkthroughs.
export default function WalkthroughNav({
  index, count, onPrevious, onNext, onFinish, finishLabel = 'Back to research', finishHref,
}) {
  const last = index === count - 1

  return (
    <>
      <div className="onboarding-dots">
        {Array.from({ length: count }, (_, dot) => (
          <span key={dot} aria-hidden="true" className={dot === index ? 'active' : undefined} />
        ))}
        <span className="visually-hidden" aria-live="polite">Step {index + 1} of {count}</span>
      </div>
      <div className="onboarding-nav">
        <button type="button" className="btn btn-secondary" disabled={index === 0}
          onClick={onPrevious}>Previous</button>
        {last
          ? finishHref
            ? <a className="btn btn-primary" href={finishHref}>{finishLabel}</a>
            : <button type="button" className="btn btn-primary" onClick={onFinish}>{finishLabel}</button>
          : <button type="button" className="btn btn-primary" onClick={onNext}>Next</button>}
      </div>
    </>
  )
}
