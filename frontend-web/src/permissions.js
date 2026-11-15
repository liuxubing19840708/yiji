import { getSession } from './api'

export function permissionSet() {
  const session = getSession()
  const company = session.companies?.find((item) => item.id === session.companyId)
  const post = company?.post || {}
  const permissions = post.effective_perms || post.permissions || []
  return {
    admin: Boolean(post.is_admin || post.permissions?.includes('*') || post.effective_perms?.includes('*')),
    permissions,
  }
}

export function hasPermission(permission) {
  const { admin, permissions } = permissionSet()
  return admin || permissions.includes(permission)
}
