import { useCallback, useEffect, useState } from 'react'

import { api } from '../api'

const ROLE_HELP = {
  viewer: 'reads results, maps, PDFs and downloads',
  analyst: 'also runs analyses and uploads',
  admin: 'also manages users',
}

/** User management for administrators: create, change role, disable, delete. */
export default function AdminUsers({ me, onClose, inline = false }) {
  const [users, setUsers] = useState([])
  const [draft, setDraft] = useState({ username: '', password: '', role: 'viewer' })
  const [message, setMessage] = useState('')

  const load = useCallback(() => {
    api.users().then((body) => setUsers(body.users)).catch((err) => setMessage(err.message))
  }, [])
  useEffect(load, [load])

  const act = async (promise, done) => {
    setMessage('')
    try {
      await promise
      if (done) setMessage(done)
      load()
    } catch (err) {
      setMessage(err.message)
    }
  }

  return (
    <aside className={inline ? 'how-inline admin-users' : 'how-it-works admin-users'} role={inline ? undefined : 'dialog'} aria-label="Users">
      {!inline && (
        <div className="hiw-head">
          <h2>Users</h2>
          <button type="button" onClick={onClose} aria-label="Close">×</button>
        </div>
      )}
      <table className="hiw-table">
        <thead><tr><th>User</th><th>Who</th><th>Role</th><th>Last sign-in</th><th /></tr></thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.username} className={u.active ? '' : 'user-disabled'}>
              <td>
                {u.full_name || u.username}{!u.active && <small> disabled</small>}
                {u.full_name && <small>{u.username}</small>}
              </td>
              <td>{u.user_type_label || '—'}{u.organisation && <small>{u.organisation}</small>}</td>
              <td>
                <select value={u.role} disabled={u.username === me?.username}
                  onChange={(e) => act(api.updateUser(u.username, { role: e.target.value }), `${u.username} is now ${e.target.value}`)}>
                  {Object.keys(ROLE_HELP).map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </td>
              <td>{u.last_login ? u.last_login.replace('T', ' ').replace('Z', ' UTC') : 'never'}</td>
              <td className="user-actions">
                {u.username !== me?.username && (
                  <>
                    <button type="button" onClick={() => act(api.updateUser(u.username, { active: !u.active }))}>
                      {u.active ? 'Disable' : 'Enable'}
                    </button>
                    <button type="button" onClick={() => {
                      if (window.confirm(`Delete ${u.username}?`)) act(api.deleteUser(u.username), `${u.username} deleted`)
                    }}>Delete</button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Add a user</h3>
      <form className="add-user" onSubmit={(e) => {
        e.preventDefault()
        act(api.createUser(draft), `${draft.username} created`).then(() => setDraft({ username: '', password: '', role: 'viewer' }))
      }}>
        <input placeholder="username" value={draft.username} onChange={(e) => setDraft({ ...draft, username: e.target.value })} />
        <input type="password" placeholder="password (10+ characters)" autoComplete="new-password" value={draft.password}
          onChange={(e) => setDraft({ ...draft, password: e.target.value })} />
        <select value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value })}>
          {Object.keys(ROLE_HELP).map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button type="submit" className="run">Add</button>
      </form>
      <small className="hiw-small">{ROLE_HELP[draft.role]}.</small>
      {message && <p className="admin-message">{message}</p>}
    </aside>
  )
}
