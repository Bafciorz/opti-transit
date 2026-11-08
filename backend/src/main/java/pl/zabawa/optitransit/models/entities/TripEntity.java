package pl.zabawa.optitransit.models.entities;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.*;

@Entity
@Table(name = "gtfs_trips")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
@ToString
@EqualsAndHashCode(onlyExplicitlyIncluded = true)
public class TripEntity {

    @Id
    @EqualsAndHashCode.Include
    @Column(name = "trip_id", nullable = false)
    private String id;

    @Column(name = "route_id", nullable = false)
    private String routeId;

    @Column(name = "service_id", nullable = false)
    private String serviceId;

    @Column(name = "trip_headsign")
    private String headsign;

    @Column(name = "direction_id")
    private Integer directionId;

    @Column(name = "wheelchair_accessible")
    private Integer wheelchairAccessible;
}
