package com.bass.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.bass.app.LogEntry
import com.bass.app.R
import com.bass.app.ui.components.BassCard
import com.bass.app.ui.components.ChipTone
import com.bass.app.ui.components.SectionTitle
import com.bass.app.ui.components.StatusChip
import java.text.DateFormat
import java.util.Date

@Composable
fun LogsScreen(
    logs: List<LogEntry>,
    modifier: Modifier = Modifier,
) {
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            BassCard {
                SectionTitle(
                    title = stringResource(R.string.logs_title),
                    description = stringResource(R.string.logs_description),
                    action = {
                        StatusChip(
                            text = logs.size.toString(),
                            tone = ChipTone.INFO,
                        )
                    },
                )
                if (logs.isEmpty()) {
                    Text(
                        text = stringResource(R.string.logs_empty),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        items(
            items = logs,
            key = { it.id },
        ) { entry ->
            LogCard(entry)
        }
    }
}

@Composable
private fun LogCard(entry: LogEntry) {
    val timestamp = DateFormat.getDateTimeInstance(
        DateFormat.MEDIUM,
        DateFormat.SHORT,
    ).format(Date(entry.timestampMillis))

    BassCard {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    text = entry.type,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    text = timestamp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.labelSmall,
                )
            }
            StatusChip(
                text = stringResource(
                    if (entry.ok) R.string.logs_ok else R.string.logs_failed,
                ),
                tone = if (entry.ok) ChipTone.SUCCESS else ChipTone.ERROR,
            )
        }
        if (entry.detail.isNotBlank()) {
            Text(
                text = entry.detail,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

