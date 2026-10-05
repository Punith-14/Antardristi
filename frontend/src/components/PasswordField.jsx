import { useState } from 'react'

import { RULES, checks, isCommon, strength } from '../lib/password'
import Icon from './Icon'

const STRENGTH_LABELS = ['Too weak', 'Weak', 'Almost there', 'Strong', 'Very strong']

/**
 * A new-password input with show/hide and a checklist that ticks as the
 * person types, so the rules are never a surprise after pressing the button.
 */
export default function PasswordField({ label = 'Password', value, onChange, invalid, error, autoFocus = false,
  placeholder = 'At least 8 characters', id = 'new-password' }) {
  const [show, setShow] = useState(false)
  const passed = checks(value)
  const common = value && Object.values(passed).every(Boolean) && isCommon(value)
  const score = strength(value)

  return (
    <div className="field">
      <label htmlFor={id}><span>{label}</span></label>
      <div className="password-field">
        <input id={id} type={show ? 'text' : 'password'} value={value} onChange={onChange}
          autoComplete="new-password" placeholder={placeholder} aria-invalid={invalid} autoFocus={autoFocus}
          aria-describedby={`${id}-rules`} />
        <button type="button" onClick={() => setShow((s) => !s)}>{show ? 'Hide' : 'Show'}</button>
      </div>
      <div className={`strength strength-${score}`} aria-hidden="true"><i /><i /><i /><i /></div>
      <ul className="pw-rules" id={`${id}-rules`}>
        {RULES.map((rule) => (
          <li key={rule.key} className={passed[rule.key] ? 'ok' : ''}>
            <Icon name={passed[rule.key] ? 'check' : 'close'} size={13} /> {rule.label}
          </li>
        ))}
        {common && <li className="bad"><Icon name="close" size={13} /> Not a commonly used password</li>}
      </ul>
      {value && <small className="pw-score">{STRENGTH_LABELS[score]}</small>}
      {error && <small className="field-error">{error}</small>}
    </div>
  )
}
