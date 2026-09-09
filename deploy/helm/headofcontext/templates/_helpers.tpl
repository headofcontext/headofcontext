{{- define "headofcontext.name" -}}
{{- .Chart.Name -}}
{{- end -}}
{{- define "headofcontext.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "headofcontext.labels" -}}
app.kubernetes.io/name: {{ include "headofcontext.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{- define "headofcontext.secretName" -}}
{{- if .Values.existingSecret -}}{{ .Values.existingSecret }}{{- else -}}{{ include "headofcontext.fullname" . }}{{- end -}}
{{- end -}}

{{- define "headofcontext.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}
