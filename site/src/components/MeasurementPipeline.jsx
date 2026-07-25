import { strategyMeasurements } from '../data/content'

export default function MeasurementPipeline() {
  return (
    <figure className="measurement-pipeline" aria-labelledby="measurement-title">
      <figcaption>
        <span className="section-label">Signal instrumentation</span>
        <h2 id="measurement-title">8 measurements → 0 or 1 candidate</h2>
        <p>Every number has a capture source and a rejection effect.</p>
      </figcaption>
      <div className="measurement-groups">
        {['Observe', 'Narrow'].map((stage) => (
          <section key={stage} className={`measurement-group journey-${stage.toLowerCase()}`}>
            <header>
              <span>{stage === 'Observe' ? '01' : '02'}</span>
              <h3>{stage}</h3>
              <strong>{strategyMeasurements.filter((item) => item.stage === stage).length}</strong>
              <small>measurements</small>
            </header>
            <ol>
              {strategyMeasurements
                .filter((item) => item.stage === stage)
                .map((item) => (
                  <li key={item.measure}>
                    <div className="measurement-value">
                      <strong>{item.value}</strong>
                      <span>{item.unit}</span>
                    </div>
                    <div className="measurement-name">
                      <strong>{item.measure}</strong>
                      <dl>
                        <div><dt>Capture</dt><dd>{item.capture}</dd></div>
                        <div><dt>Action</dt><dd>{item.operation}</dd></div>
                      </dl>
                    </div>
                  </li>
                ))}
            </ol>
          </section>
        ))}
      </div>
    </figure>
  )
}