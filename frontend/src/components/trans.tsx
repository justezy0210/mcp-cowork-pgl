import { Fragment, type ReactNode } from "react"
import { t } from "@/lib/i18n"

// Keep links and code as React nodes so translations never become executable HTML.
export function Trans({ text, values }: { text: string; values: Record<string, ReactNode> }) {
  return <>{t(text).split(/(\{[a-zA-Z0-9_]+\})/g).map((part, index) => <Fragment key={index}>{Object.hasOwn(values, part.slice(1, -1)) && part.startsWith("{") ? values[part.slice(1, -1)] : part}</Fragment>)}</>
}
