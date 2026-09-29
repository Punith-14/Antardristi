"""
Orchestration: question in, evidence-bound answer out.

    routing   plain language into a structured query - rules first, model
              second, and the model's proposal validated before it is acted on
    analysis  runs the analysis and builds the evidence record
    report    writes the prose, which is then checked against the evidence

The order matters. Every step produces something the next step can check,
and a step that fails validation is discarded rather than used.
"""
