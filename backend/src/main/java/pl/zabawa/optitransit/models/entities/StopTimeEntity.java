package pl.zabawa.optitransit.models.entities;

import jakarta.persistence.Column;
import jakarta.persistence.EmbeddedId;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
import lombok.*;

@Entity
@Table(name = "gtfs_stop_times")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
@ToString
@EqualsAndHashCode(onlyExplicitlyIncluded = true)
public class StopTimeEntity {

    @EmbeddedId
    @EqualsAndHashCode.Include
    private StopTimeId id;

    @Column(name = "stop_id", nullable = false)
    private String stopId;

    @Column(name = "arrival_time", nullable = false)
    private String arrivalTime;

    @Column(name = "departure_time", nullable = false)
    private String departureTime;
}
