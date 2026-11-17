{% if not CloudMemoryPlatformEnabled or not WorkspaceMemoryDisabled %}<memory_paths>
{% if not CloudMemoryPlatformEnabled %}user-level memory directory: {{ WorkbuddyUserMemoryDir }}
{% endif %}{% if not WorkspaceMemoryDisabled %}workspace memory directory: {{ WorkbuddyMemoryDir }}
{% endif %}</memory_paths>

{% endif %}
