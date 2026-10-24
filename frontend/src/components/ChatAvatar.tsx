type ChatAvatarProps = { role: 'user' | 'assistant' };

export default function ChatAvatar({ role }: ChatAvatarProps) {
  return <span className={`chat-avatar ${role}`} aria-hidden="true">
    {role === 'assistant' ? 'S' : <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0 2c-4.42 0-8 2.24-8 5v1h16v-1c0-2.76-3.58-5-8-5Z" /></svg>}
  </span>;
}
