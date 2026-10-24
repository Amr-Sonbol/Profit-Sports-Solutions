import { useState } from 'react';
import { StyleSheet, View } from 'react-native';
import Svg, { Circle, Line, Polyline } from 'react-native-svg';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

// The website's chart colour for "completed" (static/css/base.css).
export const CHART_COLOR = '#eb6834';

const HEIGHT = 140;
const PAD = 8;

/**
 * Tasks completed per month as a line, like the website's My progress:
 * a point per month, the selected one's value shown above the chart (the
 * latest by default; tap a month to pick another).
 */
export function LineChart({ months, top }: { months: { start: string; completed: number }[]; top: number }) {
  const theme = useTheme();
  const [width, setWidth] = useState(0);
  const [selected, setSelected] = useState(months.length - 1);
  const scale = Math.max(top, 1);
  const x = (index: number) => PAD + ((index + 0.5) * (width - PAD * 2)) / months.length;
  const y = (value: number) => PAD + (1 - value / scale) * (HEIGHT - PAD * 2);
  const chosen = months[Math.min(selected, months.length - 1)];

  return (
    <ThemedView style={styles.wrap} onLayout={(event) => setWidth(event.nativeEvent.layout.width)}>
      {chosen ? (
        <ThemedText type="small" themeColor="textSecondary">
          {monthLabel(chosen.start, true)}: <ThemedText type="smallBold">{chosen.completed}</ThemedText>
        </ThemedText>
      ) : null}
      {width > 0 ? (
        <Svg width={width} height={HEIGHT}>
          {[0, 0.5, 1].map((share) => (
            <Line
              key={share} x1={0} x2={width} y1={y(scale * share)} y2={y(scale * share)}
              stroke={theme.backgroundSelected} strokeWidth={1}
            />
          ))}
          <Polyline
            points={months.map((month, index) => `${x(index)},${y(month.completed)}`).join(' ')}
            fill="none" stroke={CHART_COLOR} strokeWidth={2} strokeLinejoin="round"
          />
          {months.map((month, index) => (
            <Circle
              key={month.start} cx={x(index)} cy={y(month.completed)} r={index === selected ? 6 : 4}
              fill={CHART_COLOR} stroke={theme.background} strokeWidth={2}
              onPress={() => setSelected(index)}
            />
          ))}
        </Svg>
      ) : <View style={{ height: HEIGHT }} />}
      <View style={styles.labels}>
        {months.map((month, index) => (
          <ThemedText
            key={month.start} type="small" style={styles.label}
            themeColor={index === selected ? 'text' : 'textSecondary'}
            onPress={() => setSelected(index)}
          >
            {monthLabel(month.start)}
          </ThemedText>
        ))}
      </View>
    </ThemedView>
  );
}

/** A small line of the year, for a row on Team progress. */
export function Sparkline({ values, width = 84, height = 26 }: { values: number[]; width?: number; height?: number }) {
  const top = Math.max(...values, 1);
  const points = values
    .map((value, index) => `${((index + 0.5) * width) / values.length},${height - 2 - (value / top) * (height - 4)}`)
    .join(' ');
  return (
    <Svg width={width} height={height}>
      <Polyline points={points} fill="none" stroke={CHART_COLOR} strokeWidth={2} strokeLinejoin="round" />
    </Svg>
  );
}

/** "Mar", or "March 2026" in full. */
export function monthLabel(start: string, full = false) {
  const date = new Date(`${start}T00:00:00`);
  return date.toLocaleDateString(undefined, full ? { month: 'long', year: 'numeric' } : { month: 'short' });
}

const styles = StyleSheet.create({
  wrap: { gap: Spacing.one, backgroundColor: 'transparent' },
  labels: { flexDirection: 'row' },
  label: { flex: 1, textAlign: 'center', fontSize: 10 },
});
