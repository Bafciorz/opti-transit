package pl.zabawa.optitransit.models.entities;

import jakarta.persistence.*;
import lombok.*;

import java.time.Instant;

@Entity
@Table(name = "feed_metadata")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
@ToString
@EqualsAndHashCode(onlyExplicitlyIncluded = true)
public class FeedMetadataEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @EqualsAndHashCode.Include
    private Long id;

    @Column(name = "feed_version", nullable = false)
    private String feedVersion;

    @Column(name = "loaded_at")
    private Instant loadedAt;

    @Column(name = "stops_count")
    private Integer stopsCount;

    @Column(name = "routes_count")
    private Integer routesCount;

    @Column(name = "trips_count")
    private Integer tripsCount;
}
