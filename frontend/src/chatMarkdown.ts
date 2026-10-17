import MarkdownIt from 'markdown-it'

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: false,
})

const defaultLinkOpen = markdown.renderer.rules.link_open
markdown.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index].attrSet('rel', 'noreferrer noopener')
  return defaultLinkOpen
    ? defaultLinkOpen(tokens, index, options, env, self)
    : self.renderToken(tokens, index, options)
}

export function renderChatMarkdown(source: string): string {
  return markdown.render(source || '')
}
