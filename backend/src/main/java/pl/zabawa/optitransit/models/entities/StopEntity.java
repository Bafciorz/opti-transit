package pl.zabawa.optitransit.models.entities;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.*;

@Entity
@Table(name = "gtfs_stops")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
@ToString
@EqualsAndHashCode(onlyExplicitlyIncluded = true)
public class StopEntity {

    @Id
    @EqualsAndHashCode.Include
    @Column(name = "stop_id", nullable = false)
    private String id;

    @Column(name = "stop_name", nullable = false)
    private String name;

    @Column(name = "stop_code")
    private String code;

    @Column(name = "platform_code")
    private String platformCode;

    @Column(name = "stop_lat", nullable = false)
    private Double latitude;

    @Column(name = "stop_lon", nullable = false)
    private Double longitude;

    @Column(name = "town_name")
    private String townName;

    @Column(name = "wheelchair_boarding")
    private Integer wheelchairBoarding;
}
